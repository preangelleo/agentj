"""Explicit ICS subscription; private URL lives only in the existing named key card.
No Google OAuth fallback: google.py currently provides planning/runtime, not calendar authorization.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import http.client
import ipaddress
import re
import socket
import ssl
import urllib.parse
from zoneinfo import ZoneInfo
from . import jcards_cal as cal

LIMIT = 262144
NAME = re.compile(r'[a-z][a-z0-9-]{1,31}')
# P428 F1: the ICS URL key card must be a calendar one; any other key-card value never becomes a URL we fetch.
KEY = re.compile(r'(CALENDAR|ICS)_[A-Z0-9_]{0,48}_SECRET')


def public(host, port=443):
    """Every DNS answer must be a global address (P413); the first is the one we connect to. → address or ValueError."""
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError('bad_url')
    return answers[0][4][0]


def pinned(host, address, port=443, timeout=10):
    """HTTPS to the pinned address with SNI + hostname verification; http.client never reads proxy env."""
    class Pinned(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection((address, port), timeout=timeout)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=host)
            except BaseException:
                raw.close()
                raise
    return Pinned(host, port, timeout=timeout, context=ssl.create_default_context())


def fetch(url):
    """HTTPS only, pin verified public DNS address, no proxy, redirect or error-body echo."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or u.port not in (None, 443) or any(ord(c) < 33 for c in url):
        raise ValueError('bad_url')
    c = pinned(u.hostname, public(u.hostname))
    try:
        c.request('GET', urllib.parse.urlunsplit(('', '', u.path or '/', u.query, '')), headers={'Accept': 'text/calendar'})
        r = c.getresponse()
        if r.status != 200:
            raise ValueError('upstream_failed')
        body = r.read(LIMIT + 1)
        if len(body) > LIMIT:
            raise ValueError('too_large')
        return body
    finally:
        c.close()


def when(prop):
    """(params, value) of DTSTART/DTEND → (iso, all_day): DATE, UTC `Z`, TZID via zoneinfo, floating → the local zone."""
    params, value = prop
    if 'VALUE=DATE' in params or len(value) == 8:
        return dt.datetime.strptime(value, '%Y%m%d').date().isoformat(), True
    t = dt.datetime.strptime(value.rstrip('Z'), '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        t = t.replace(tzinfo=dt.timezone.utc)
    else:
        z = next((p[5:] for p in params if p.startswith('TZID=')), None)
        if z:
            t = t.replace(tzinfo=ZoneInfo(z.strip('"')))
        else:
            t = t.astimezone()
    return t.isoformat(), False


def unescape(s):
    return re.sub(r'\\([nN,;\\])', lambda m: '\n' if m[1].lower() == 'n' else m[1], s)


def vevents(data):
    """Each VEVENT as {NAME: (params, value)} of its own lines, in order; the first VALARM's lines under '_alarm'.
    Nested components are otherwise skipped; structure errors raise bad_ics where they are met."""
    lines = re.split(r'\r?\n', re.sub(r'\r?\n[ \t]', '', data))     # P428 F2: only CRLF / LF end a line
    if 'BEGIN:VCALENDAR' not in lines or 'END:VCALENDAR' not in lines:
        raise ValueError('bad_ics')
    current, depth, alarm = None, 0, None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            if current is not None:
                raise ValueError('bad_ics')
            current, depth = {}, 0
        elif current is not None:
            if line.startswith('BEGIN:'):
                depth += 1
                alarm = current.setdefault('_alarm', {}) if depth == 1 and line == 'BEGIN:VALARM' and '_alarm' not in current else None
            elif line.startswith('END:') and depth:
                depth -= 1
                alarm = None
            elif line == 'END:VEVENT':
                p, current = current, None
                yield p
            elif not depth or alarm is not None:
                key, sep, value = line.partition(':')
                if not sep and not depth:
                    raise ValueError('bad_ics')
                parts = key.split(';')
                (alarm if depth else current)[parts[0]] = (parts[1:], value)
    if current is not None:
        raise ValueError('bad_ics')


def parse(raw, name):
    if len(raw) > LIMIT:
        raise ValueError('too_large')
    events = []
    for p in vevents(raw.decode('utf-8-sig')):
        if p.get('STATUS', ([], ''))[1] == 'CANCELLED':
            continue
        if any(k in p for k in ('RRULE', 'RDATE', 'EXDATE', 'RECURRENCE-ID')):
            raise ValueError('recurrence_unsupported')
        uid = p['UID'][1]
        if 'DURATION' in p:
            raise ValueError('duration_unsupported')
        start, all_day = when(p['DTSTART'])
        end, end_day = when(p['DTEND']) if 'DTEND' in p else (None, all_day)
        if all_day != end_day:
            raise ValueError('bad_ics')
        e = cal.event({'title': cal.clean(unescape(p.get('SUMMARY', ([], 'Untitled'))[1])), 'start': start, 'end': end,
                       'all_day': all_day, 'note': cal.clean(unescape(p.get('DESCRIPTION', ([], ''))[1])), 'source': 'ICS: ' + name})
        ident = 'ics-' + hashlib.sha256((name + '\n' + uid).encode()).hexdigest()[:32]
        if any(x['id'] == ident for x in events):
            raise ValueError('duplicate_uid')
        events.append({**e, 'id': ident, 'subscription': name})
        if len(events) > cal.MAX_EVENTS:
            raise ValueError('full')
    return events


def command(root, op, name, key=None, get=None, read_key=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return {'ok': False, 'why': 'bad_name'}
    if op == 'subscribe':
        if not isinstance(key, str) or not KEY.fullmatch(key):
            return {'ok': False, 'why': 'bad_key_name'}
        def update(doc):
            subs = doc.setdefault('subscriptions', {})
            if name not in subs and len(subs) >= 8:
                raise ValueError('full')
            subs[name] = {'enabled': True, 'key': key}
        return cal.transaction(root, update)
    if op == 'unsubscribe':
        def update(doc):
            doc.setdefault('subscriptions', {}).pop(name, None)
            doc['events'] = [e for e in doc['events'] if not isinstance(e, dict) or e.get('subscription') != name]
        return cal.transaction(root, update)
    try:
        doc, revision = cal.load(root)
        sub = doc.get('subscriptions', {}).get(name, {})
        if not sub.get('enabled'):
            return {'ok': False, 'why': 'disabled'}
        if not isinstance(sub.get('key'), str) or not KEY.fullmatch(sub['key']):
            raise ValueError('bad_key_name')
        if read_key is None:
            from . import keys
            read_key = lambda n: keys.read_env().get(n)
        url = read_key(sub['key'])
        if not url:
            return {'ok': False, 'why': 'key_missing'}
        incoming = parse((get or fetch)(url), name)
        old = [e for e in doc['events'] if isinstance(e, dict) and e.get('subscription') == name]
        def update(doc):
            doc['events'] = [e for e in doc['events'] if not isinstance(e, dict) or e.get('subscription') != name] + incoming
        res = cal.transaction(root, update, revision)
        if res['ok']:
            res['remote_wins'] = old != incoming
        return res
    except Exception:
        # No URL, upstream text or key ever reaches the CLI / paired channel / log.
        return {'ok': False, 'why': 'sync_failed'}
