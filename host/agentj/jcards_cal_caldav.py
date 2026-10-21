"""P428 item 3 (CALENDAR-SYNC-SPEC §5): the CalDAV adapter of the two-way sync engine — Apple iCloud, any HTTPS + Basic server.

P428 F1 (elevate.sock is shared by the fenced Agent and the owner's terminal, so a request never chooses where a key-card
value goes): key-card NAMES must be `ICLOUD_…_SECRET` / `CALDAV_…_SECRET`, and the server must be caldav.icloud.com or a host
the owner listed in `<state>/calendar/caldav-hosts` (outside the Agent fence) — both checked on every adapter build, before
any key is read or any request is made. HTTPS only to public addresses (P413 `public`/`pinned`: every DNS answer global, TLS
SNI + hostname verified, no proxy env); Basic auth; responses ≤ 1 MiB, 15 s timeouts; ≤ 3 redirects, https only, to the same
host or (from caldav.icloud.com only) to *.icloud.com — any other hop is refused before the credentials leave. XML that is
not UTF-8 or carries a DOCTYPE / ENTITY is refused before parsing. Only fixed words leave: never a URL, href, user,
password, body or header. The transport is injectable (`transport(method, url, headers, body) → (status, headers, body)`).
"""
from __future__ import annotations

import base64
import datetime as dt
import http.client
import os
import re
import stat
import ssl
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

from . import jcards_cal as cal, privacy
from .jcards_cal_remote import ROWTAG, RemoteError, sendable, tag
from .jcards_cal_sync import public, pinned, unescape, vevents, when

LIMIT, TIMEOUT = 1 << 20, 15
D, C = "{DAV:}", "{urn:ietf:params:xml:ns:caldav}"
NS = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
REDIRECT = (301, 302, 303, 307, 308)
OWN = ("SUMMARY", "DTSTART", "DTEND", "DURATION", "DESCRIPTION")      # the lines an update rewrites (+ the first VALARM)
DAV_KEY = re.compile(r"(ICLOUD|CALDAV)_[A-Z0-9_]{0,48}_SECRET")        # F1: the only key cards a CalDAV login may use
ICLOUD = "caldav.icloud.com"                                           # F1: the one built-in server
HOSTS, HOSTS_MAX = ("calendar", "caldav-hosts"), 4096                  # F1: the owner's own list, <state>/calendar/
HOSTNAME = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")   # never an IP literal


def https(method, url, headers, body):
    """The real transport: one request to the pinned public address of the URL's host."""
    u = urllib.parse.urlsplit(url)
    try:
        c = pinned(u.hostname, public(u.hostname, u.port or 443), u.port or 443, TIMEOUT)
    except ValueError:
        raise RemoteError("failed") from None                   # a private / reserved address
    except OSError:
        raise RemoteError("offline") from None
    try:
        c.request(method, urllib.parse.urlunsplit(("", "", u.path or "/", u.query, "")), body=body, headers=headers)
        r = c.getresponse()
        data = r.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise RemoteError("failed")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data
    except ssl.SSLCertVerificationError:
        raise RemoteError("failed") from None
    except (OSError, http.client.HTTPException):
        raise RemoteError("offline") from None
    finally:
        c.close()


TRANSPORT = https


def _secret(name):
    from . import keys
    return keys.read_env().get(name)


def word(status: int) -> str:
    return {401: "auth", 403: "auth", 412: "conflict", 404: "gone", 410: "gone", 429: "offline"}.get(
        status, "offline" if status >= 500 else "failed")


def server_url(v) -> str:
    """The owner's server: https, a host, no userinfo / query / fragment / control characters. → normalised URL."""
    try:
        u = urllib.parse.urlsplit(v) if isinstance(v, str) and len(v) <= 300 else None
        if not u or u.scheme != "https" or not u.hostname or "@" in u.netloc or u.query or u.fragment or "?" in v \
                or "#" in v or any(ord(c) < 33 or ord(c) == 127 for c in v):
            raise ValueError()
        u.port
    except ValueError:
        raise ValueError("bad_server") from None
    return urllib.parse.urlunsplit(("https", u.netloc.lower(), u.path or "/", "", ""))


def owner_hosts(st) -> frozenset:
    """F1: `<state>/calendar/caldav-hosts` — one hostname per line (lowercased), `#` comments; opened without following a link
    at either level, regular, ours, ≤ 4 KiB. Missing / anything odd → no extra host (fail closed)."""
    root = getattr(st, "root", None)
    nofollow, cloexec = getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_CLOEXEC", 0)
    try:
        d = os.open(root, os.O_RDONLY | os.O_DIRECTORY | cloexec)
        try:
            c = os.open(HOSTS[0], os.O_RDONLY | os.O_DIRECTORY | nofollow | cloexec, dir_fd=d)
        finally:
            os.close(d)
        try:
            fd = os.open(HOSTS[1], os.O_RDONLY | os.O_NONBLOCK | nofollow | cloexec, dir_fd=c)
        finally:
            os.close(c)
    except (OSError, TypeError, ValueError):
        return frozenset()
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > HOSTS_MAX:
            return frozenset()
        data = b""
        while len(data) <= HOSTS_MAX:
            chunk = os.read(fd, HOSTS_MAX + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > HOSTS_MAX:
            return frozenset()
        rows = data.decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return frozenset()
    finally:
        os.close(fd)
    return frozenset(h for h in (r.split("#", 1)[0].strip().lower() for r in rows) if HOSTNAME.fullmatch(h))


def allowed(st, host) -> bool:
    return host == ICLOUD or (isinstance(host, str) and host in owner_hosts(st))


def bound(st, host, names) -> bool:
    """N3: a key namespace goes with its servers — ICLOUD_… only to caldav.icloud.com, CALDAV_… only to an owner-listed host."""
    return all(host == ICLOUD if k.startswith("ICLOUD_") else isinstance(host, str) and host in owner_hosts(st) for k in names)


def path_ok(p) -> bool:
    """N2: an absolute path without `.` / `..` segments (raw or percent-encoded), encoded slashes / backslashes or `//`."""
    return isinstance(p, str) and p.startswith("/") and not p.startswith("//") and len(p) <= 1024 \
        and not re.search(r"[\s?#\\]|%(?:2e|2f|5c|25)", p, re.I) and not any(x in (".", "..") for x in p.split("/"))


def same_site(server_host: str, host) -> bool:
    """F6: a request or redirect stays on the server's own host; only caldav.icloud.com may hand over to *.icloud.com."""
    s, h = str(server_host or "").lower(), str(host or "").lower()
    return bool(h) and (h == s or (s == ICLOUD and h.endswith(".icloud.com") and bool(HOSTNAME.fullmatch(h))))


def _xml(data: bytes):
    """F10: UTF-8 only (a UTF-16/32 BOM, a NUL or another declared encoding is refused), then no DOCTYPE / ENTITY."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data:
        raise RemoteError("failed")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RemoteError("failed") from None
    m = re.match(r"\s*<\?xml[^>]*?\bencoding\s*=\s*[\"']([^\"']*)[\"']", text)
    if m and m[1].strip().lower() not in ("utf-8", "utf8"):
        raise RemoteError("failed")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise RemoteError("failed")                              # XXE / billion laughs: never parsed
    try:
        return ET.fromstring(data)
    except Exception:
        raise RemoteError("failed") from None


def _txt(v, n: int) -> tuple:
    """Remote text → (card text, lossy): lossy when redaction, truncation or control clean-up changed it (F5)."""
    raw = unescape(v)
    out = privacy.redact(privacy.redact(cal.clean(raw), owner=True)[:n], owner=True)[:n].strip()
    return out, out != raw.strip()


def duration(v: str) -> dt.timedelta:
    m = re.fullmatch(r"([+-]?)P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v or "")
    if not m or not any(m.groups()[1:]):
        raise ValueError("bad_duration")
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    t = dt.timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -t if m[1] == "-" else t


def reminder(alarm) -> int | None:
    params, v = (alarm or {}).get("TRIGGER", ([], ""))
    if not v or any(p.upper() in ("VALUE=DATE-TIME", "RELATED=END") for p in params):
        return None
    try:
        t = -duration(v)
    except ValueError:
        return None
    mins = int(t.total_seconds() // 60)
    return mins if 0 <= mins <= 40320 else None


def item(rid: str, etag: str, text: str) -> dict | None:
    """One calendar resource → an engine row; recurring → read-only master (first occurrence, not expanded); cancelled → None."""
    out = {"rid": rid, "etag": tag(etag), "ok": False}
    try:
        evs = list(vevents(text))
        if not evs:
            return out
        p = next((e for e in evs if "RECURRENCE-ID" not in e), evs[0])
        if p.get("STATUS", ([], ""))[1].upper() == "CANCELLED":
            return None
        start, day = when(p["DTSTART"])
        if "DTEND" in p:
            end, end_day = when(p["DTEND"])
            if end_day != day:
                return out
        elif "DURATION" in p:
            span = duration(p["DURATION"][1])
            end = (dt.date.fromisoformat(start) + dt.timedelta(days=max(span.days, 1))).isoformat() if day \
                else (dt.datetime.fromisoformat(start) + span).isoformat()
        else:
            end = None
        (title, lt), (note, ln) = _txt(p.get("SUMMARY", ([], ""))[1], 300), _txt(p.get("DESCRIPTION", ([], ""))[1], 1000)
        out.update(cal.event({"title": title or "(no title)", "start": start, "end": end, "all_day": day, "note": note,
                              "reminder": reminder(p.get("_alarm"))}), ok=True)
        lossy = [f for f, bad in (("title", lt or not title), ("note", ln)) if bad]
        if lossy:
            out["lossy"] = lossy
        if any(k in e for e in evs for k in ("RRULE", "RDATE", "RECURRENCE-ID")):
            out["readonly"] = True
    except (ValueError, KeyError, TypeError, OverflowError):
        pass                                                     # seen, not shown: a linked row is never deleted for it
    return out


# ------------------------------------------------------------------ iCalendar writing (RFC 5545 escaping + folding)
def esc(s: str) -> str:
    """RFC 5545 TEXT: every line break (CRLF, CR, LF, NEL, U+2028/2029) → \\n, other controls dropped (F2)."""
    s = cal.clean(s or "")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line: str) -> str:
    """75 octets per physical line, never splitting a UTF-8 character; continuation lines start with one space."""
    out, cur, n = [], "", 0
    for ch in line:
        k = len(ch.encode())
        if n + k > (74 if out else 75):
            out.append(cur)
            cur, n = "", 0
        cur, n = cur + ch, n + k
    return "\r\n ".join(out + [cur])


def _stamp(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def own(fields) -> tuple:
    """F5: the changed card fields → (properties an update rewrites, rewrite the first VALARM?). None = everything."""
    if fields is None:
        return set(OWN), True
    f = set(fields)
    out = ({"SUMMARY"} if "title" in f else set()) | ({"DESCRIPTION"} if "note" in f else set())
    if f & {"start", "end", "all_day"}:
        out |= {"DTSTART", "DTEND", "DURATION"}
    return out, "reminder" in f


def props(ev: dict, names=OWN, alarm: bool = True) -> list:
    if ev.get("all_day"):
        day = lambda v: dt.date.fromisoformat(v).strftime("%Y%m%d")      # N1: parsed, never string surgery on stored text
        span = ["DTSTART;VALUE=DATE:" + day(ev["start"]), "DTEND;VALUE=DATE:" + day(ev["end"])]
    else:
        span = ["DTSTART:" + _stamp(cal.instant(ev["start"])), "DTEND:" + _stamp(cal.instant(ev["end"]))]
    out = (["SUMMARY:" + esc(ev["title"])] if "SUMMARY" in names else []) + (span if "DTSTART" in names else []) \
        + (["DESCRIPTION:" + esc(ev["note"])] if ev.get("note") and "DESCRIPTION" in names else [])
    if alarm and ev.get("reminder") is not None:
        out += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + esc(ev["title"]), "TRIGGER:-PT%dM" % ev["reminder"], "END:VALARM"]
    return out


def ics(lines: list) -> bytes:
    return ("\r\n".join(fold(x) for x in lines) + "\r\n").encode()


def new(ev: dict, uid: str) -> bytes:
    return ics(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Agent J//Calendar//EN", "BEGIN:VEVENT", "UID:" + uid + "@agentj",
                "DTSTAMP:" + _stamp(cal.now()), *props(ev), "END:VEVENT", "END:VCALENDAR"])


def patch(text: str, ev: dict, fields=None) -> bytes:
    """Rewrite only the properties of the changed fields (F5; None = SUMMARY / DTSTART / DTEND / DURATION / DESCRIPTION and
    the first VALARM) of the first VEVENT; every other line stays. Lines end only at CRLF / LF (F2)."""
    names, alarm = own(fields)
    lines = [x for x in re.split(r"\r?\n", re.sub(r"\r?\n[ \t]", "", text)) if x]
    out, state, depth, skip, done = [], 0, 0, 0, False         # state 0 before, 1 inside the first VEVENT, 2 after
    for line in lines:
        if state == 1:
            if skip:                                           # inside the first VALARM: dropped
                skip += 1 if line.startswith("BEGIN:") else -1 if line.startswith("END:") else 0
                continue
            if line.startswith("BEGIN:"):
                if not depth and line == "BEGIN:VALARM" and alarm and not done:
                    done, skip = True, 1
                    continue
                depth += 1
            elif line.startswith("END:") and depth:
                depth -= 1
            elif line == "END:VEVENT":
                out += props(ev, names, alarm)
                state = 2
            elif not depth and re.split(r"[;:]", line, maxsplit=1)[0].upper() in names:
                continue
        elif state == 0 and line == "BEGIN:VEVENT":
            state = 1
        out.append(line)
    if state != 2:
        raise RemoteError("failed")
    return ics(out)


# ------------------------------------------------------------------ the adapter
PRINCIPAL = '<d:propfind %s><d:prop><d:current-user-principal/></d:prop></d:propfind>' % NS
HOME = '<d:propfind %s><d:prop><c:calendar-home-set/></d:prop></d:propfind>' % NS
CALS = ('<d:propfind %s><d:prop><d:resourcetype/><d:displayname/><c:supported-calendar-component-set/></d:prop>'
        '</d:propfind>' % NS)
ETAG = '<d:propfind %s><d:prop><d:getetag/></d:prop></d:propfind>' % NS
QUERY = ('<c:calendar-query %s><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
         '<c:comp-filter name="VEVENT"><c:time-range start="%%s" end="%%s"/></c:comp-filter></c:comp-filter></c:filter>'
         '</c:calendar-query>' % NS)


class CalDAV:
    kind = "caldav"

    def __init__(self, st, sub: dict, transport=None, read_key=None):
        """Every build (connect and each sync: calendar.json is visible to the Agent) checks the key names and the server
        allowlist first; only then are the two values read (F1)."""
        self.server = server_url(sub.get("server"))
        self.host = urllib.parse.urlsplit(self.server).hostname
        names = (sub.get("user_key"), sub.get("pass_key"))
        if not all(isinstance(k, str) and DAV_KEY.fullmatch(k) for k in names):
            raise ValueError("bad_key_name")
        if not allowed(st, self.host):
            raise ValueError("server_not_allowed")
        if not bound(st, self.host, names):
            raise ValueError("bad_key_name")
        origin = sub.get("origin") or self.server
        if not same_site(self.host, urllib.parse.urlsplit(origin).hostname):
            raise ValueError("bad_server")
        self.origin = server_url(origin)
        self.cal = sub.get("calendar")
        if self.cal is not None and not (isinstance(self.cal, str) and re.fullmatch(r"/[^\s?#]{0,500}/", self.cal)
                                         and path_ok(self.cal)):
            raise ValueError("bad_calendar")
        user, pw = ((read_key or _secret)(k) for k in names)
        if not user or not pw:
            raise ValueError("key_missing")
        self._auth = "Basic " + base64.b64encode((user + ":" + pw).encode()).decode()
        self.transport = transport or TRANSPORT

    # --- HTTP with the redirect rule
    def _req(self, method: str, url: str, body=None, depth=None, **extra):
        hdr = {"User-Agent": "AgentJ-CalDAV/1", **extra}
        if depth is not None:
            hdr["Depth"] = depth
        if body is not None:
            hdr["Content-Type"] = "text/calendar; charset=utf-8" if method == "PUT" else "application/xml; charset=utf-8"
            body = body if isinstance(body, bytes) else ('<?xml version="1.0" encoding="utf-8"?>' + body).encode()
        for hop in range(4):
            u = urllib.parse.urlsplit(url)
            if u.scheme != "https" or u.username or u.password or u.fragment or not same_site(self.host, u.hostname) \
                    or any(ord(c) < 33 for c in url):
                raise RemoteError("failed")                     # never a request (nor the credentials) to another site
            try:
                status, h, data = self.transport(method, url, {**hdr, "Authorization": self._auth}, body)
            except RemoteError:
                raise
            except Exception:
                raise RemoteError("failed") from None
            h = {str(k).lower(): v for k, v in (h or {}).items()}
            if status not in REDIRECT:
                return status, h, data or b"", url
            loc = h.get("location")
            if not loc or hop == 3:
                raise RemoteError("failed")
            url = urllib.parse.urljoin(url, loc)
        raise RemoteError("failed")

    def _multi(self, method, url, body, depth):
        status, _, data, final = self._req(method, url, body, depth)
        if status != 207:
            raise RemoteError(word(status))
        rows = []
        for r in _xml(data).iter(D + "response"):
            href = (r.findtext(D + "href") or "").strip()
            if not href:
                continue
            got = {}
            for ps in r.findall(D + "propstat"):
                if " 200" in (ps.findtext(D + "status") or ""):
                    for p in ps.iter(D + "prop"):
                        got.update({el.tag: el for el in p})
            rows.append((urllib.parse.urljoin(final, href), got, final))
        return rows

    def _href(self, url, body, prop) -> str:
        for _, got, final in self._multi("PROPFIND", url, body, "0"):
            href = (got.get(prop).findtext(D + "href") or "").strip() if got.get(prop) is not None else ""
            if href:
                return urllib.parse.urljoin(final, href)
        raise RemoteError("failed")

    def discover(self, name=None) -> dict:
        """current-user-principal (/.well-known/caldav first) → calendar-home-set → Depth 1 VEVENT calendars → `name` / first."""
        principal = None
        for start in (urllib.parse.urljoin(self.server, "/.well-known/caldav"), self.server):
            try:
                principal = self._href(start, PRINCIPAL, D + "current-user-principal")
                break
            except RemoteError as e:
                if e.word in ("auth", "offline"):
                    raise
        if principal is None:
            raise RemoteError("failed")
        home = self._href(principal, HOME, C + "calendar-home-set")
        found = []
        for href, got, _ in self._multi("PROPFIND", home, CALS, "1"):
            kinds = got.get(D + "resourcetype")
            comps = got.get(C + "supported-calendar-component-set")
            if kinds is None or kinds.find(C + "calendar") is None:
                continue
            if comps is not None and "VEVENT" not in [x.get("name", "").upper() for x in comps.findall(C + "comp")]:
                continue
            found.append(((got.get(D + "displayname").text or "") if got.get(D + "displayname") is not None else "", href))
        pick = [f for f in found if name is None or f[0].strip().casefold() == name.strip().casefold()]
        if not pick:
            raise ValueError("no_calendar")
        u = urllib.parse.urlsplit(pick[0][1])
        if not same_site(self.host, u.hostname):
            raise RemoteError("failed")
        path = u.path if u.path.endswith("/") else u.path + "/"
        return {"origin": "https://" + u.netloc.lower().removesuffix(":443"), "calendar": path}

    # --- the engine's four calls
    def rid_ok(self, rid) -> bool:
        """N2: `<calendar>/<name>.ics` — one resource directly in the calendar collection, nothing that walks out of it."""
        return bool(self.cal) and path_ok(rid) and rid.startswith(self.cal) and rid.endswith(".ics") \
            and len(rid) > len(self.cal) + 4 and "/" not in rid[len(self.cal):]

    def _url(self, rid) -> str:
        if not self.rid_ok(rid):
            raise RemoteError("failed")
        return urllib.parse.urljoin(self.origin, rid)

    def list(self, lo, hi) -> list:
        if not self.cal:
            raise RemoteError("failed")
        rows = []
        for href, got, _ in self._multi("REPORT", urllib.parse.urljoin(self.origin, self.cal),
                                     QUERY % (_stamp(lo), _stamp(hi)), "1"):
            data, et = got.get(C + "calendar-data"), got.get(D + "getetag")
            rid = urllib.parse.urlsplit(href).path
            if data is None or not self.rid_ok(rid):
                continue                                         # N2: never a row we could not write back to
            row = item(rid, (et.text or "").strip() if et is not None else "", data.text or "")
            if row is not None:
                rows.append(row)
        return rows

    def _etag(self, url, h) -> str:
        if h.get("etag"):
            return tag(h["etag"])
        try:
            for _, got, _ in self._multi("PROPFIND", url, ETAG, "0"):
                if got.get(D + "getetag") is not None:
                    return tag((got[D + "getetag"].text or "").strip())
        except RemoteError:
            pass
        return ""                                                # the next pull rebases it

    def _put(self, url, body, **cond) -> str:
        status, h, _, _ = self._req("PUT", url, body, **cond)
        if status not in (200, 201, 204):
            raise RemoteError(word(status))
        return self._etag(url, h)

    def create(self, ev: dict) -> dict:
        ev = sendable(ev)                                        # N1: rebuilt through cal.event, never the raw row
        uid = uuid.uuid4().hex
        rid = self.cal + uid + ".ics"
        url = self._url(rid)
        return {"rid": rid, "etag": self._put(url, new(ev, uid), **{"If-None-Match": "*"})}

    def update(self, rid, etag, ev: dict, fields=None) -> dict:
        ev, url = sendable(ev), self._url(rid)
        if not (etag is None or isinstance(etag, str) and ROWTAG.fullmatch(etag)):
            raise RemoteError("failed")                          # N1: never a header value from an unchecked row
        status, h, data, _ = self._req("GET", url)
        if status != 200:
            raise RemoteError(word(status))
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise RemoteError("failed") from None
        return {"etag": self._put(url, patch(text, ev, fields), **{"If-Match": etag or h.get("etag") or "*"})}

    def delete(self, rid, etag) -> None:
        if not (etag is None or isinstance(etag, str) and ROWTAG.fullmatch(etag)):
            raise RemoteError("failed")
        status, _, _, _ = self._req("DELETE", self._url(rid), **({"If-Match": etag} if etag else {}))
        if status not in (200, 202, 204):
            raise RemoteError(word(status))


def setup(st, server, user_key, pass_key, calendar=None, transport=None, read_key=None) -> dict:
    """connect: check the names, read the two values, run discovery. → the subscription's CalDAV fields (names, never values)."""
    if calendar is not None and not (isinstance(calendar, str) and 0 < len(calendar) <= 200
                                     and not any(ord(c) < 32 for c in calendar)):
        raise ValueError("bad_calendar")
    sub = {"server": server_url(server), "user_key": user_key, "pass_key": pass_key}
    found = CalDAV(st, sub, transport, read_key).discover(calendar)
    return {**sub, **found}
