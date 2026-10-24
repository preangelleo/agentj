import _hermetic  # noqa: F401: isolated test HOME/state before importing the host
"""Business sync tests use an explicit trusted approval callback; real UV is covered by P503.

P428 item 3: the CalDAV adapter (Apple iCloud write-back) against an in-process fake CalDAV server — no real network."""
import base64
import json
import pathlib
import re
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import jcards, jcards_cal as cal, jcards_cal_caldav as dav, jcards_cal_remote as rm  # noqa: E402
from agentj import jcards_cal_sync as ics  # noqa: E402

USER, PASSWORD = 'owner.p428@example.org', 'abcd-efgh-ijkl-mnop'
KEYS = {'ICLOUD_USER_SECRET': USER, 'ICLOUD_APP_PASSWORD_SECRET': PASSWORD}
SERVER = 'https://caldav.icloud.com/'
HOME = '/1234567/calendars/'
WORK = HOME + 'work-cal-7f3a/'
NOW = cal.instant('2026-10-20T00:00:00+00:00')
T = {'title': 'Meeting', 'start': '2026-10-20T09:30:00+00:00', 'end': '2026-10-20T10:30:00+00:00'}


def vcal(*lines):
    return '\r\n'.join(['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Apple Inc.//iCloud//EN', *lines, 'END:VCALENDAR']) + '\r\n'


def vevent(uid, *lines):
    return ['BEGIN:VEVENT', 'UID:' + uid, 'DTSTAMP:20261001T000000Z', *lines, 'END:VEVENT']


def ms(*responses):
    body = ''.join('<d:response><d:href>%s</d:href><d:propstat><d:prop>%s</d:prop><d:status>HTTP/1.1 200 OK</d:status>'
                   '</d:propstat></d:response>' % r for r in responses)
    return ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">%s</d:multistatus>'
            % body).encode()


class FakeDAV:
    """iCloud-shaped: the entry host redirects /.well-known/caldav to a partition host; resources with etags;
    If-Match / If-None-Match; Basic auth checked on every request; failures injectable."""

    def __init__(self, entry='caldav.icloud.com', part='p42-caldav.icloud.com', redirect_to=None):
        self.entry, self.part, self.redirect_to = entry, part, redirect_to
        self.res, self.calls, self.n, self.fail, self.etag_header, self.after_get = {}, [], 0, {}, True, None
        self.auth = 'Basic ' + base64.b64encode((USER + ':' + PASSWORD).encode()).decode()
        self.cals = [('Work', WORK, 'VEVENT'), ('Reminders', HOME + 'tasks/', 'VTODO'), ('Home', HOME + 'home/', None)]

    def put(self, path, text):
        self.n += 1
        self.res[path] = ('"e%d"' % self.n, text)

    def __call__(self, method, url, headers, body):
        u = urllib.parse.urlsplit(url)
        self.calls.append((method, u.hostname, u.path, dict(headers), body))
        if method in self.fail:
            f = self.fail[method]
            if isinstance(f, Exception):
                raise f
            return f, {}, b'<html>server says ' + USER.encode() + b'</html>'
        if headers.get('Authorization') != self.auth:
            return 401, {'WWW-Authenticate': 'Basic realm="x"'}, b''
        if u.path == '/.well-known/caldav':
            return 301, {'Location': self.redirect_to or 'https://%s/' % self.part}, b''
        if u.hostname != self.part:
            return (301, {'Location': self.redirect_to}, b'') if self.redirect_to else (404, {}, b'')
        p, b = u.path, (body or b'').decode()
        if method == 'PROPFIND':
            if 'current-user-principal' in b:
                return 207, {}, ms((p, '<d:current-user-principal><d:href>/1234567/principal/</d:href></d:current-user-principal>'))
            if 'calendar-home-set' in b:
                return 207, {}, ms((p, '<c:calendar-home-set><d:href>https://%s:443%s</d:href></c:calendar-home-set>' % (self.part, HOME)))
            if 'resourcetype' in b:
                rows = [(HOME, '<d:resourcetype><d:collection/></d:resourcetype>')]
                for name, href, comp in self.cals:
                    rows.append((href, '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>%s</d:displayname>%s'
                                 % (name, '' if comp is None else '<c:supported-calendar-component-set><c:comp name="%s"/>'
                                    '</c:supported-calendar-component-set>' % comp)))
                return 207, {}, ms(*rows)
            if 'getetag' in b and p in self.res:
                return 207, {}, ms((p, '<d:getetag>%s</d:getetag>' % self.res[p][0]))
            return 404, {}, b''
        if method == 'REPORT':
            assert headers.get('Depth') == '1' and re.search(r'time-range start="\d{8}T\d{6}Z" end="\d{8}T\d{6}Z"', b), b
            rows = [(k, '<d:getetag>%s</d:getetag><c:calendar-data>%s</c:calendar-data>'
                     % (e, t.replace('&', '&amp;').replace('<', '&lt;'))) for k, (e, t) in self.res.items() if k.startswith(p)]
            return 207, {}, ms(*rows)
        cur = self.res.get(p)
        if method == 'GET':
            if cur is None:
                return 404, {}, b''
            out = 200, {'ETag': cur[0]}, cur[1].encode()
            if self.after_get:
                self.after_get(p)
            return out
        if method == 'PUT':
            if headers.get('If-None-Match') == '*' and cur is not None:
                return 412, {}, b''
            if 'If-Match' in headers and (cur is None or headers['If-Match'] != cur[0]):
                return 412, {}, b''
            self.put(p, body.decode())
            return 201 if cur is None else 204, ({'ETag': self.res[p][0]} if self.etag_header else {}), b''
        if method == 'DELETE':
            if cur is None:
                return 404, {}, b''
            if 'If-Match' in headers and headers['If-Match'] != cur[0]:
                return 412, {}, b''
            del self.res[p]
            return 204, {}, b''
        return 405, {}, b''


class Base(unittest.TestCase):
    def setUp(self):
        from test_l1 import _state
        self.tmp = tempfile.TemporaryDirectory(prefix='p428dav-')
        self.addCleanup(self.tmp.cleanup)
        self.st = _state(pathlib.Path(self.tmp.name) / 'h')
        self.root = self.st.root
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', {'schema': 1, 'events': [], 'subscriptions': {}})
        self.fake = FakeDAV()
        self.keys = dict(KEYS)
        for p in (mock.patch.object(dav, 'TRANSPORT', self.fake), mock.patch.object(dav, '_secret', lambda n: self.keys.get(n)),
                  mock.patch.object(cal, 'now', lambda: NOW)):
            p.start()
            self.addCleanup(p.stop)
        self.results = []

    def ok(self, res):
        self.results.append(res)
        self.assertTrue(res.get('ok'), res)
        return res

    def connect(self, **kw):
        req = {'op': 'connect', 'kind': 'caldav', 'name': 'icloud', 'server': SERVER, 'user_key': 'ICLOUD_USER_SECRET',
               'pass_key': 'ICLOUD_APP_PASSWORD_SECRET', **kw}
        self.assertIsNone(rm.request_shape({'t': 'calsync', **req}))
        res = rm.host_op(self.st, req)
        self.results.append(res)
        return res

    def sync(self):
        res = rm.sync(self.root, 'icloud', rm.adapter_for(self.st, self.doc()['subscriptions']['icloud']), NOW, authorize=lambda *_: True)
        self.results.append(res)
        return res

    def doc(self):
        return cal.load(self.root)[0]

    def rows(self):
        return {e['title']: e for e in cal.listing(self.root)['events']}

    def ev(self, ident):
        return next(e for e in self.doc()['events'] if e['id'] == ident)

    def puts(self, method='PUT'):
        return [c for c in self.fake.calls if c[0] == method]


class Discovery(Base):
    def test_icloud_redirect_picks_vevent_calendar_and_stores_names_only(self):
        r = self.connect()
        self.assertTrue(r['ok'] and r['sync']['ok'], r)
        first = self.fake.calls[0]
        self.assertEqual(first[:3], ('PROPFIND', 'caldav.icloud.com', '/.well-known/caldav'))
        self.assertEqual(first[3]['Depth'], '0')
        self.assertTrue(all(c[1] in ('caldav.icloud.com', 'p42-caldav.icloud.com') for c in self.fake.calls))
        sub = self.doc()['subscriptions']['icloud']
        self.assertEqual({k: sub[k] for k in ('kind', 'server', 'origin', 'calendar', 'user_key', 'pass_key')},
                         {'kind': 'caldav', 'server': SERVER, 'origin': 'https://p42-caldav.icloud.com', 'calendar': WORK,
                          'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'})
        self.assertNotIn(USER, json.dumps(self.doc()))
        self.assertNotIn(PASSWORD, json.dumps(self.doc()))
        row = cal.listing(self.root)['subscriptions'][0]
        self.assertEqual(set(row), {'name', 'kind', 'enabled', 'last_sync', 'last_error', 'queued', 'conflicts', 'push_new', 'auto'})

    def test_named_calendar_without_component_set_and_unknown_name(self):
        self.assertTrue(self.connect(calendar='home')['ok'])
        self.assertEqual(self.doc()['subscriptions']['icloud']['calendar'], HOME + 'home/')
        self.assertEqual(self.connect(calendar='Reminders')['why'], 'no_calendar')     # VTODO-only: not a choice

    def test_cross_domain_redirect_refused_and_never_contacted(self):
        self.fake = FakeDAV(redirect_to='https://caldav.icloud.com.evil.example/steal')
        with mock.patch.object(dav, 'TRANSPORT', self.fake):
            r = self.connect()
        self.assertEqual((r['ok'], r['why']), (False, 'failed'))
        self.assertTrue(self.fake.calls)
        self.assertEqual({c[1] for c in self.fake.calls}, {'caldav.icloud.com'})     # Authorization never went there
        self.assertNotIn('icloud', self.doc()['subscriptions'])
        # http redirects and > 3 hops are refused too
        for loc in ('http://p42-caldav.icloud.com/', 'https://user:pw@p42-caldav.icloud.com/'):
            fake = FakeDAV(redirect_to=loc)
            with mock.patch.object(dav, 'TRANSPORT', fake):
                self.assertEqual(self.connect()['why'], 'failed')
            self.assertEqual({c[1] for c in fake.calls}, {'caldav.icloud.com'})
        loop = FakeDAV(part='nowhere.icloud.com', redirect_to='https://p1-caldav.icloud.com/')
        with mock.patch.object(dav, 'TRANSPORT', loop):
            self.assertEqual(self.connect()['why'], 'failed')
        self.assertLessEqual(sum(1 for c in loop.calls if c[1] == 'p1-caldav.icloud.com'), 8)

    def test_bad_server_key_names_and_missing_values(self):
        for bad in ('http://caldav.icloud.com/', 'https://u:p@caldav.icloud.com/', 'https://caldav.icloud.com/?x=1',
                    'https://caldav.icloud.com/#f', 'https://caldav.icloud.com/a b', 'caldav.icloud.com', None, 'https://'):
            self.assertEqual(rm.connect(self.st, 'caldav', 'icloud', server=bad, user_key='ICLOUD_USER_SECRET',
                                        pass_key='ICLOUD_APP_PASSWORD_SECRET')['why'], 'bad_server', bad)
        self.assertEqual(self.connect(user_key='ICLOUD_USER')['why'], 'bad_key_name')
        self.assertEqual(self.connect(pass_key='-x_SECRET')['why'], 'bad_key_name')
        del self.keys['ICLOUD_APP_PASSWORD_SECRET']
        self.assertEqual(self.connect()['why'], 'key_missing')
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(rm.request_shape({'t': 'calsync', 'op': 'connect', 'name': 'x1', 'kind': 'caldav', 'server': 1}),
                         'kind: google | caldav; push_new / auto: booleans; calendar: a string')

    def test_wrong_password_is_auth(self):
        self.keys['ICLOUD_APP_PASSWORD_SECRET'] = 'wrong'
        self.assertEqual(self.connect()['why'], 'auth')

    def test_private_address_and_http_refused_by_the_real_transport(self):
        with mock.patch.object(ics.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.8', 443))]), \
                mock.patch.object(ics.socket, 'create_connection') as conn:
            with self.assertRaises(rm.RemoteError) as e:
                dav.https('PROPFIND', 'https://caldav.icloud.com/', {}, b'')
            self.assertEqual(e.exception.word, 'failed')
            conn.assert_not_called()
        with mock.patch.object(ics.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('17.1.2.3', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(rm.RemoteError):
                dav.https('GET', 'https://caldav.icloud.com/', {}, None)
        with mock.patch.object(ics.socket, 'getaddrinfo', side_effect=OSError('down')):
            with self.assertRaises(rm.RemoteError) as e:
                dav.https('GET', 'https://caldav.icloud.com/', {}, None)
            self.assertEqual(e.exception.word, 'offline')
        a = dav.CalDAV(self.st, {'server': SERVER, 'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET',
                                 'calendar': WORK}, transport=self.fake)
        for url in ('http://caldav.icloud.com/', 'https://example.com/'):
            with self.assertRaises(rm.RemoteError):
                a._req('GET', url)
        self.assertEqual(self.fake.calls, [])
        with self.assertRaises(ValueError):
            dav.CalDAV(self.st, {'server': SERVER, 'origin': 'https://evil.example/', 'user_key': 'ICLOUD_USER_SECRET',
                                 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'})
        self.assertTrue(dav.same_site('caldav.icloud.com', 'p07-caldav.icloud.com'))
        self.assertFalse(dav.same_site('caldav.icloud.com', 'eviIcloud.com'))
        self.assertFalse(dav.same_site('17.1.2.3', '17.1.2.4'))

    def test_doctype_and_entity_refused_before_parsing(self):
        for x in (b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><d:multistatus xmlns:d="DAV:">&a;</d:multistatus>',
                  b'<d:multistatus xmlns:d="DAV:"><!ENTITY x SYSTEM "file:///etc/passwd"></d:multistatus>', b'<<<'):
            with self.assertRaises(rm.RemoteError):
                dav._xml(x)
        with mock.patch.object(dav.ET, 'fromstring') as parse:
            with self.assertRaises(rm.RemoteError):
                dav._xml(b'<!doctype x>')
            parse.assert_not_called()
        bad = FakeDAV()
        orig = bad.__call__
        bad_call = lambda m, u, h, b: (207, {}, b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>') \
            if m == 'PROPFIND' else orig(m, u, h, b)
        with mock.patch.object(dav, 'TRANSPORT', bad_call):
            self.assertEqual(self.connect()['why'], 'failed')


class Pull(Base):
    def test_mapping(self):
        self.fake.put(WORK + 'a.ics', vcal(*vevent('a', 'SUMMARY:Holiday', 'DTSTART;VALUE=DATE:20261022', 'DTEND;VALUE=DATE:20261024')))
        self.fake.put(WORK + 'b.ics', vcal('BEGIN:VTIMEZONE', 'TZID:Asia/Tokyo', 'END:VTIMEZONE', *vevent(
            'b', 'SUMMARY:Tokyo call\\, prep', 'DTSTART;TZID=Asia/Tokyo:20261020T180000', 'DTEND;TZID=Asia/Tokyo:20261020T190000',
            'DESCRIPTION:line1\\nline2', 'BEGIN:VALARM', 'ACTION:DISPLAY', 'TRIGGER:-PT15M', 'END:VALARM',
            'BEGIN:VALARM', 'ACTION:AUDIO', 'TRIGGER:-PT5M', 'END:VALARM')))
        self.fake.put(WORK + 'c.ics', vcal(*vevent('c', 'SUMMARY:Floating', 'DTSTART:20261021T090000', 'DTEND:20261021T100000',
                                                   'BEGIN:VALARM', 'TRIGGER:-PT1H', 'END:VALARM')))
        self.fake.put(WORK + 'd.ics', vcal(*vevent('d', 'SUMMARY:Ninety', 'DTSTART:20261023T010000Z', 'DURATION:PT1H30M',
                                                   'BEGIN:VALARM', 'TRIGGER:-P1D', 'END:VALARM')))
        self.fake.put(WORK + 'e.ics', vcal(*vevent('e', 'SUMMARY:Weekly', 'DTSTART:20261020T120000Z', 'DTEND:20261020T130000Z',
                                                   'RRULE:FREQ=WEEKLY'),
                                           *vevent('e', 'SUMMARY:Weekly moved', 'RECURRENCE-ID:20261027T120000Z',
                                                   'DTSTART:20261027T140000Z', 'DTEND:20261027T150000Z')))
        self.fake.put(WORK + 'f.ics', vcal(*vevent('f', 'SUMMARY:Called off', 'STATUS:CANCELLED', 'DTSTART:20261024T010000Z',
                                                   'DTEND:20261024T020000Z')))
        self.fake.put(WORK + 'g.ics', vcal(*vevent('g', 'SUMMARY:Bad zone', 'DTSTART;TZID=Mars/Olympus:20261020T010000',
                                                   'DTEND;TZID=Mars/Olympus:20261020T020000')))
        self.fake.put(WORK + 'h.ics', vcal(*vevent('h', 'DTSTART;VALUE=DATE:20261025', 'DURATION:P2D')))
        self.fake.put(HOME + 'home/x.ics', vcal(*vevent('x', 'SUMMARY:Other calendar', 'DTSTART:20261020T010000Z')))
        r = self.ok(self.connect())
        self.assertEqual(r['sync']['pulled'], 6)
        rows = self.rows()
        self.assertEqual(set(rows), {'Holiday', 'Tokyo call, prep', 'Floating', 'Ninety', 'Weekly', '(no title)'})
        self.assertEqual((rows['Holiday']['all_day'], rows['Holiday']['start'], rows['Holiday']['end']), (True, '2026-10-22', '2026-10-24'))
        tk = rows['Tokyo call, prep']
        self.assertEqual((tk['start'], tk['end'], tk['reminder'], tk['note']),
                         ('2026-10-20T18:00:00+09:00', '2026-10-20T19:00:00+09:00', 15, 'line1\nline2'))
        fl = rows['Floating']
        self.assertEqual(cal.instant(fl['start']).utcoffset(), cal.instant(fl['start']).astimezone().utcoffset())   # local zone
        self.assertTrue(fl['start'].startswith('2026-10-21T09:00:00'))
        self.assertEqual(fl['reminder'], 60)
        self.assertEqual((rows['Ninety']['end'], rows['Ninety']['reminder']), ('2026-10-23T02:30:00+00:00', 1440))
        self.assertEqual((rows['(no title)']['start'], rows['(no title)']['end']), ('2026-10-25', '2026-10-27'))
        wk = rows['Weekly']
        self.assertTrue(wk['readonly'])
        self.assertEqual(wk['start'], '2026-10-20T12:00:00+00:00')
        self.assertEqual(cal.change(self.root, 'edit', wk['id'], title='x')['why'], 'readonly')

    def test_reminder_and_duration_parsing(self):
        cases = {'-PT15M': 15, '-PT2H': 120, '-P1D': 1440, 'PT0S': 0, '-P1W': 10080, 'PT5M': None, '-P40D': None, 'junk': None}
        for v, want in cases.items():
            self.assertEqual(dav.reminder({'TRIGGER': ([], v)}), want, v)
        self.assertIsNone(dav.reminder({'TRIGGER': (['VALUE=DATE-TIME'], '20261020T000000Z')}))
        self.assertIsNone(dav.reminder({'TRIGGER': (['RELATED=END'], '-PT5M')}))
        self.assertIsNone(dav.reminder(None))


class Push(Base):
    def setUp(self):
        super().setUp()
        self.ok(self.connect(push_new=True))
        self.fake.calls.clear()

    def test_create_update_preserving_lines_delete(self):
        e = self.ok(cal.change(self.root, 'add', **(T | {'reminder': 10, 'note': 'agenda'})))['events'][0]
        self.assertEqual(e['remote'], 'icloud')
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        put = self.puts()[0]
        self.assertEqual(put[3]['If-None-Match'], '*')
        self.assertNotIn('If-Match', put[3])
        self.assertTrue(re.fullmatch(re.escape(WORK) + r'[0-9a-f]{32}\.ics', put[2]))
        body = put[4].decode()
        uid = put[2].rsplit('/', 1)[1][:-4]
        for line in ('PRODID:-//Agent J//Calendar//EN', 'UID:%s@agentj' % uid, 'SUMMARY:Meeting', 'DESCRIPTION:agenda',
                     'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z', 'ACTION:DISPLAY', 'TRIGGER:-PT10M', 'DTSTAMP:20261020T000000Z'):
            self.assertIn('\r\n' + line + '\r\n', body)
        rid = self.ev(e['id'])['rid']
        self.assertEqual((rid, self.ev(e['id'])['etag']), (put[2], self.fake.res[put[2]][0]))
        # an owner edit on iCloud adds attendees / location / X- lines and its own alarm
        etag, text = self.fake.res[rid]
        extra = ['LOCATION:Room 4\\, floor 2', 'ATTENDEE;CN=Kim;PARTSTAT=ACCEPTED:mailto:kim@example.net', 'X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC']
        self.fake.put(rid, text.replace('END:VEVENT', '\r\n'.join(extra) + '\r\nEND:VEVENT'))
        self.ok(self.sync())
        self.ok(cal.change(self.root, 'edit', e['id'], title='Moved', start='2026-10-21T09:00:00+00:00',
                           end='2026-10-21T10:00:00+00:00', reminder=None))
        before = self.fake.res[rid][0]
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        up = self.puts()[-1]
        self.assertEqual(up[3]['If-Match'], before)
        body = up[4].decode()
        for line in extra + ['UID:%s@agentj' % uid, 'SUMMARY:Moved', 'DTSTART:20261021T090000Z']:
            self.assertIn('\r\n' + line + '\r\n', body)
        self.assertEqual(body.count('SUMMARY:'), 1)
        self.assertEqual(body.count('DTEND:'), 1)
        self.assertNotIn('VALARM', body)                                # reminder cleared → the alarm went
        self.assertNotIn('DESCRIPTION:agenda\r\nDESCRIPTION', body)
        self.assertEqual(self.ev(e['id'])['etag'], self.fake.res[rid][0])
        self.assertEqual(self.ok(self.sync())['pushed'], 0)             # settled: nothing more to send
        # delete with If-Match
        self.ok(cal.change(self.root, 'delete', e['id']))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        d = self.puts('DELETE')[-1]
        self.assertEqual(d[3]['If-Match'], self.fake.calls[-1][3]['If-Match'])
        self.assertNotIn(rid, self.fake.res)
        # a delete of something already gone remotely is done
        e2 = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.ok(self.sync())
        rid2 = self.ev(e2['id'])['rid']
        self.ok(cal.change(self.root, 'delete', e2['id']))
        del self.fake.res[rid2]
        self.ok(self.sync())
        self.assertEqual(self.doc()['outbox'], [])

    def test_missing_etag_header_falls_back_to_propfind(self):
        self.fake.etag_header = False
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.ok(self.sync())
        rid = self.ev(e['id'])['rid']
        self.assertEqual(self.ev(e['id'])['etag'], self.fake.res[rid][0])

    def test_412_marks_conflict_then_next_sync_remote_wins_local_saved(self):
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.ok(self.sync())
        rid = self.ev(e['id'])['rid']
        self.ok(cal.change(self.root, 'edit', e['id'], title='Mine'))
        def race(path):                                          # the owner edits on the iPhone between our GET and PUT
            self.fake.after_get = None
            self.fake.put(path, self.fake.res[path][1].replace('SUMMARY:Meeting', 'SUMMARY:Theirs'))
        self.fake.after_get = race
        r = self.ok(self.sync())
        self.assertEqual((r['pushed'], r['queued']), (0, 1))
        self.assertEqual(self.doc()['outbox'][0]['error'], 'conflict')
        r = self.ok(self.sync())
        self.assertEqual((r['queued'], r['conflicts']), (0, 1))
        self.assertEqual(self.rows()['Theirs']['conflict'], True)
        self.assertEqual(self.doc()['conflicts'][0]['local']['title'], 'Mine')
        self.assertIn('SUMMARY:Theirs', self.fake.res[rid][1])

    def test_offline_and_auth_keep_the_queue(self):
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        for fail, why in ((OSError('net down'), 'failed'), (rm.RemoteError('offline'), 'offline'), (503, 'offline'), (401, 'auth')):
            self.fake.fail = {'REPORT': fail}
            r = rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)
            self.results.append(r)
            self.assertEqual((r['ok'], r['why'], r['queued']), (False, why, 1), fail)
        self.assertEqual(r.get('offline'), None)
        self.fake.fail = {'PUT': 503}
        r = rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)
        self.results.append(r)
        self.assertEqual((r['why'], r['offline'], r['queued']), ('offline', True, 1))
        self.assertGreaterEqual(self.doc()['outbox'][0]['tries'], 2)
        self.fake.fail = {}
        self.keys['ICLOUD_APP_PASSWORD_SECRET'] = 'revoked'
        r = rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)
        self.results.append(r)
        self.assertEqual((r['why'], r['queued']), ('auth', 1))
        self.assertEqual(cal.listing(self.root)['subscriptions'][0]['last_error'], 'auth')
        del self.keys['ICLOUD_USER_SECRET']
        self.assertEqual(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)['why'], 'key_missing')
        self.keys.update(KEYS)
        self.assertEqual(self.ok(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True))['pushed'], 1)
        self.assertTrue(self.ev(e['id'])['rid'])

    def test_escaping_folding_round_trip_cjk_and_dash_title(self):
        title = ('会议,;\\纪要' * 40)[:200]
        note = 'a,b;c\\d\nsecond line'
        e = self.ok(cal.change(self.root, 'add', **(T | {'title': title, 'note': note})))['events'][0]
        f = self.ok(cal.change(self.root, 'add', **(T | {'title': '-rf --all', 'start': '2026-10-22T09:00:00+00:00',
                                                          'end': '2026-10-22T09:30:00+00:00'})))['events'][1]
        self.assertEqual(self.ok(self.sync())['pushed'], 2)
        raw = self.puts()[0][4]
        for line in raw.split(b'\r\n'):
            self.assertLessEqual(len(line), 75)
            line.decode('utf-8')                                  # never a split character
        self.assertIn(b'\r\n ', raw)
        got = next(ics.vevents(raw.decode()))
        self.assertEqual((ics.unescape(got['SUMMARY'][1]), ics.unescape(got['DESCRIPTION'][1])), (title, note))
        self.assertIn('\r\nSUMMARY:-rf --all\r\n', self.puts()[1][4].decode())
        self.assertEqual(self.ok(self.sync())['pushed'], 0)       # the pulled copy equals ours: nothing to send back
        self.assertEqual((self.ev(e['id'])['title'], self.ev(e['id'])['note']), (title, note))
        self.assertEqual(self.ev(f['id'])['title'], '-rf --all')
        self.assertEqual(self.rows()[title]['pending'], False)

    def test_all_day_push_and_folding_unit(self):
        self.ok(cal.change(self.root, 'add', title='Trip', all_day=True, start='2026-10-25', end='2026-10-27', reminder=0))
        self.ok(self.sync())
        body = self.puts()[0][4].decode()
        self.assertIn('\r\nDTSTART;VALUE=DATE:20261025\r\nDTEND;VALUE=DATE:20261027\r\n', body)
        self.assertIn('TRIGGER:-PT0M', body)
        self.assertEqual(dav.fold('x' * 75), 'x' * 75)
        self.assertEqual(dav.fold('x' * 76), 'x' * 75 + '\r\n x')
        self.assertEqual(dav.esc('a\\b;c,d\r\ne'), 'a\\\\b\\;c\\,d\\ne')


class Secrets(Base):
    def test_nothing_secret_leaves(self):
        self.ok(self.connect(push_new=True))
        self.ok(cal.change(self.root, 'add', **T))
        self.fake.fail = {'PUT': 500}
        self.results.append(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True))
        self.fake.fail = {'REPORT': 403}
        self.results.append(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True))
        self.fake.fail = {}
        self.ok(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True))
        import asyncio
        from types import SimpleNamespace
        r = asyncio.run(rm.elevate_request(SimpleNamespace(st=self.st), self.st, {'t': 'calsync', 'op': 'sync', 'name': 'icloud'}))
        self.results.append(r)
        texts = [json.dumps(self.results), json.dumps(cal.listing(self.root)), json.dumps(rm.cli.__doc__ or '')]
        from agentj import jcards_cal_undo as undo
        texts.append(json.dumps(undo.queue_view(self.root)))
        log = self.st.log_path
        for _ in range(50):
            if log.exists():
                break
            import time
            time.sleep(0.02)
        texts.append(log.read_text() if log.exists() else '')
        for fail in (OSError('to ' + USER), ValueError(PASSWORD)):
            a = rm.adapter_for(self.st, self.doc()['subscriptions']['icloud'])
            a.transport = mock.Mock(side_effect=fail)
            with self.assertRaises(rm.RemoteError) as e:
                a.list(NOW, NOW)
            texts.append(str(e.exception) + repr(e.exception.args))
        blob = '\n'.join(texts)
        auth = base64.b64encode((USER + ':' + PASSWORD).encode()).decode()
        for secret in (USER, PASSWORD, auth, 'caldav.icloud.com', 'p42-caldav', HOME, '1234567', 'ICLOUD_USER_SECRET'):
            self.assertNotIn(secret, blob, secret)


class Allowlist(Base):
    """F1 / F6: a request never chooses where a key-card value goes — key-name namespaces and the server allowlist are checked
    before any key is read or any request is made, on connect and on every sync (calendar.json is visible to the Agent)."""

    def setUp(self):
        super().setUp()
        self.keys.update(CALDAV_WORK_USER_SECRET=USER, CALDAV_WORK_PASS_SECRET=PASSWORD, AGENTJ_HOOK_SECRET='hook-value')
        self.read = []
        p = mock.patch.object(dav, '_secret', lambda n: (self.read.append(n), self.keys.get(n))[1])
        p.start()
        self.addCleanup(p.stop)

    def elevate(self, **kw):
        import asyncio
        from types import SimpleNamespace
        req = {'t': 'calsync', 'op': 'connect', 'kind': 'caldav', 'name': 'icloud', 'server': SERVER,
               'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET', **kw}
        res = asyncio.run(rm.elevate_request(SimpleNamespace(st=self.st), self.st, req))
        self.results.append(res)
        return res

    def hosts(self, text):
        d = self.st.root / 'calendar'
        d.mkdir(exist_ok=True)
        (d / 'caldav-hosts').write_text(text)
        return d / 'caldav-hosts'

    def test_attacker_server_no_request_no_key_read(self):
        for server in ('https://attacker.example/', 'https://caldav.icloud.com.evil.example/', 'https://icloud.com/',
                       'https://p42-caldav.icloud.com/', 'https://CALDAV.ICLOUD.COM.evil.example/'):
            r = self.elevate(server=server, user_key='AGENTJ_HOOK_SECRET' if 'attacker' in server else 'ICLOUD_USER_SECRET')
            self.assertEqual((r['ok'], r['why']), (False, 'bad_key_name' if 'attacker' in server else 'server_not_allowed'), server)
            r = self.elevate(server=server)
            self.assertEqual((r['ok'], r['why']), (False, 'server_not_allowed'), server)
        self.assertEqual(self.fake.calls, [], 'no request at all')
        self.assertEqual(self.read, [], 'no key-card value read')
        self.assertEqual(self.doc()['subscriptions'], {})

    def test_wrong_namespace_refused_before_any_read(self):
        for k in ('AGENTJ_HOOK_SECRET', 'CALENDAR_ICS_SECRET', 'ICLOUD_USER', 'ICLOUDX_SECRET', 'X_ICLOUD_USER_SECRET',
                  'ICLOUD_' + 'A' * 49 + '_SECRET'):
            self.assertEqual(self.elevate(user_key=k)['why'], 'bad_key_name', k)
            self.assertEqual(self.elevate(pass_key=k)['why'], 'bad_key_name', k)
        self.assertEqual((self.read, self.fake.calls), ([], []))
        self.assertTrue(self.elevate(user_key='ICLOUD_USER_SECRET')['ok'])

    def test_tampered_card_data_refused_on_sync(self):
        self.ok(self.connect())
        self.read.clear()
        self.fake.calls.clear()
        good = self.doc()
        for change, why in (({'server': 'https://attacker.example/', 'origin': 'https://attacker.example'}, 'server_not_allowed'),
                            ({'user_key': 'AGENTJ_HOOK_SECRET'}, 'bad_key_name'),
                            ({'origin': 'https://attacker.example'}, 'bad_server'),
                            ({'origin': 'https://p1-caldav.icloud.com.evil.example'}, 'bad_server')):
            d = json.loads(json.dumps(good))
            d['subscriptions']['icloud'].update(change)
            jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
            self.assertEqual(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)['why'], why, change)
        self.assertEqual((self.read, self.fake.calls), ([], []))

    def test_owner_listed_host_and_its_redirect_rule(self):
        self.assertEqual(self.elevate(server='https://dav.example.net/', user_key='CALDAV_WORK_USER_SECRET',
                                      pass_key='CALDAV_WORK_PASS_SECRET')['why'], 'server_not_allowed')
        self.hosts('# the owner\'s own servers\nDAV.EXAMPLE.NET\n  dav.example.net   # work\nbad host.example\n10.0.0.8\n')
        self.assertEqual(dav.owner_hosts(self.st), frozenset({'dav.example.net'}))
        fake = FakeDAV(entry='dav.example.net', part='dav.example.net')
        with mock.patch.object(dav, 'TRANSPORT', fake):
            r = self.connect(server='https://dav.example.net/', user_key='CALDAV_WORK_USER_SECRET', pass_key='CALDAV_WORK_PASS_SECRET')
            self.assertTrue(r['ok'] and r['sync']['ok'], r)
        self.assertEqual({c[1] for c in fake.calls}, {'dav.example.net'})
        # an owner-listed server may not hand the credentials to another host, not even another listed one
        self.hosts('dav.example.net\np1.example.net\n')
        hop = FakeDAV(entry='dav.example.net', part='p1.example.net')
        with mock.patch.object(dav, 'TRANSPORT', hop):
            self.assertEqual(self.connect(server='https://dav.example.net/', user_key='CALDAV_WORK_USER_SECRET',
                                          pass_key='CALDAV_WORK_PASS_SECRET')['why'], 'failed')
        self.assertEqual({c[1] for c in hop.calls}, {'dav.example.net'})
        self.assertFalse(dav.same_site('dav.example.co.uk', 'evil.co.uk'))     # the old "last two labels" rule is gone
        self.assertFalse(dav.same_site('dav.example.net', 'p1-caldav.icloud.com'))
        self.assertFalse(dav.same_site('caldav.icloud.com', 'icloud.com.evil.example'))
        self.assertTrue(dav.same_site('caldav.icloud.com', 'p07-caldav.icloud.com'))

    def test_ics_key_namespace(self):
        """The ICS URL lives in a CALENDAR_… / ICS_… key card; any other key-card value never becomes a URL we fetch."""
        for bad in ('AGENTJ_HOOK_SECRET', 'ICLOUD_USER_SECRET', 'CALENDAR_ICS'):
            self.assertEqual(ics.command(self.root, 'subscribe', 'personal', bad)['why'], 'bad_key_name', bad)
        for good in ('CALENDAR_ICS_SECRET', 'ICS_WORK_SECRET'):
            self.assertTrue(ics.command(self.root, 'subscribe', 'personal', good)['ok'], good)
        d = self.doc()
        d['subscriptions']['personal']['key'] = 'AGENTJ_HOOK_SECRET'           # edited by hand / by the Agent
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        reader, get = mock.Mock(return_value='https://example.com/x.ics'), mock.Mock()
        self.assertEqual(ics.command(self.root, 'sync', 'personal', read_key=reader, get=get)['why'], 'sync_failed')
        reader.assert_not_called()
        get.assert_not_called()

    def test_hosts_file_read_without_links_and_bounded(self):
        f = self.hosts('dav.example.net\n')
        self.assertEqual(dav.owner_hosts(self.st), {'dav.example.net'})
        f.write_text('dav.example.net\n' + '#' * 5000)
        self.assertEqual(dav.owner_hosts(self.st), frozenset())
        f.unlink()
        real = self.st.root / 'elsewhere'
        real.write_text('dav.example.net\n')
        f.symlink_to(real)
        self.assertEqual(dav.owner_hosts(self.st), frozenset())
        f.unlink()
        f.parent.rmdir()
        other = self.st.root / 'other-dir'
        other.mkdir()
        (other / 'caldav-hosts').write_text('dav.example.net\n')
        (self.st.root / 'calendar').symlink_to(other)
        self.assertEqual(dav.owner_hosts(self.st), frozenset())
        from types import SimpleNamespace
        self.assertEqual(dav.owner_hosts(SimpleNamespace(root=None)), frozenset())


class Injection(Base):
    """F2: no title / note can end an iCalendar line early; remote text is cleaned, never refused."""

    def test_line_separators_never_inject(self):
        old = vcal(*vevent('u1', 'SUMMARY:Old', 'DESCRIPTION:keep', 'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z'))
        for sep in ('\r', '\x85', '\u2028', '\u2029', '\r\n', '\n', '\x0b', '\x1c'):
            ev = {**cal.event(T), 'title': 'Lunch' + sep + 'ATTENDEE:mailto:evil@example.net', 'reminder': 5,
                  'note': 'a' + sep + 'END:VEVENT' + sep + 'BEGIN:VEVENT'}
            for body in (dav.new(ev, 'u1'), dav.patch(old, ev), dav.patch(old, ev, ['title'])):
                text = body.decode()
                evs = list(ics.vevents(text))
                self.assertEqual(len(evs), 1, repr(sep))
                self.assertNotIn('ATTENDEE', evs[0])
                self.assertEqual(ics.unescape(evs[0]['SUMMARY'][1]), cal.clean(ev['title']))
                self.assertIsNone(re.search('[\x00-\x08\x0b-\x0c\x0e-\x1f\x85\u2028\u2029]|\r(?!\n)|(?<!\r)\n', text), repr(sep))
        self.assertIn('DESCRIPTION:keep', dav.patch(old, ev, ['title']).decode())     # F5: an unchanged note stays as it was

    def test_local_input_with_controls_refused(self):
        for bad in ('a\rb', 'a\x85b', 'a\u2028b', 'a\u2029b', 'a\x1bb', 'a\x00b'):
            self.assertEqual(cal.change(self.root, 'add', **(T | {'title': bad}))['why'], 'bad_text', repr(bad))
            self.assertEqual(cal.change(self.root, 'add', **(T | {'note': bad}))['why'], 'bad_text', repr(bad))
        self.assertTrue(cal.change(self.root, 'add', **(T | {'note': 'line one\n\tline two'}))['ok'])

    def test_remote_separators_cleaned_round_trip(self):
        self.ok(self.connect())
        rid = WORK + 'r1.ics'
        self.fake.put(rid, vcal(*vevent('r1', 'SUMMARY:Lunch\u2028ATTENDEE:mailto:evil@example.net', 'DESCRIPTION:a\x85b',
                                        'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z')))
        self.ok(self.sync())
        row = next(e for e in self.doc()['events'] if e.get('rid') == rid)
        self.assertEqual((row['title'], row['note'], row['lossy']), ('Lunch\nATTENDEE:mailto:evil@example.net', 'a\nb', ['title', 'note']))
        self.assertEqual(self.ok(self.sync())['pushed'], 0)
        # a bare CR the server stored inside DESCRIPTION: one line for us, replaced as one line when the note changes
        etag, text = self.fake.res[rid]
        self.fake.res[rid] = (etag, text.replace('DESCRIPTION:a\x85b', 'DESCRIPTION:old\rATTENDEE:mailto:evil@example.net'))
        self.ok(cal.change(self.root, 'edit', row['id'], note='new note'))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        body = self.puts()[-1][4].decode()
        evs = list(ics.vevents(body))
        self.assertEqual(len(evs), 1)
        self.assertNotIn('ATTENDEE', evs[0])
        self.assertEqual(ics.unescape(evs[0]['DESCRIPTION'][1]), 'new note')
        self.assertIn('SUMMARY:Lunch\u2028ATTENDEE', body)          # an untouched (lossy) title is never rewritten

    def test_ics_feed_with_separators_still_syncs(self):
        raw = vcal(*vevent('i1', 'SUMMARY:Talk\u2028x', 'DESCRIPTION:a\x85b', 'DTSTART:20261020T093000Z',
                           'DTEND:20261020T103000Z')).encode()
        ev = ics.parse(raw, 'personal')[0]
        self.assertEqual((ev['title'], ev['note']), ('Talk\nx', 'a\nb'))


class XmlEncoding(Base):
    def test_non_utf8_xml_refused_before_parsing(self):
        bodies = ('<?xml version="1.0" encoding="UTF-16"?><d:multistatus xmlns:d="DAV:"/>'.encode('utf-16'),
                  '<d:multistatus xmlns:d="DAV:"/>'.encode('utf-16-be'), '<!DOCTYPE x><x/>'.encode('utf-16-le'),
                  '<?xml version="1.0" encoding="utf-32"?><x/>'.encode('utf-32'),
                  b'<?xml version="1.0" encoding="ISO-8859-1"?><d:multistatus xmlns:d="DAV:"/>',
                  b"<?xml version='1.0' encoding='latin1'?><x/>", b'<x>\xff\xfe</x>')
        with mock.patch.object(dav.ET, 'fromstring') as parse:
            for x in bodies:
                with self.assertRaises(rm.RemoteError, msg=x):
                    dav._xml(x)
            parse.assert_not_called()
        self.assertEqual(dav._xml(b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?><d:multistatus xmlns:d="DAV:"/>').tag,
                         '{DAV:}multistatus')


class SecurityRound2(Base):
    """P428 security round 2: N1 Agent-writable rows re-validated before any write, N2 rid paths, N3 key namespace ↔ server,
    N4 connect rate gap, D6 `sync` of an unknown name."""
    BAD = '2026-10-21\r\nATTENDEE;RSVP=TRUE:mailto:v@example.com'
    elevate, hosts = Allowlist.elevate, Allowlist.hosts

    def setUp(self):
        super().setUp()
        self.keys.update(CALDAV_WORK_USER_SECRET=USER, CALDAV_WORK_PASS_SECRET=PASSWORD)
        self.read = []
        p = mock.patch.object(dav, '_secret', lambda n: (self.read.append(n), self.keys.get(n))[1])
        p.start()
        self.addCleanup(p.stop)

    def tamper(self, row, op='create', **o):
        d = self.doc()
        d['events'].append(row)
        d.setdefault('outbox', []).append({'op': op, 'id': row['id'], 'remote': 'icloud', 'at': '2026-10-20T00:00:00+00:00',
                                           'tries': 0, **o})
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)

    def test_tampered_row_never_reaches_the_server(self):
        self.ok(self.connect())
        self.fake.calls.clear()
        self.tamper({'id': 'e-bad', 'title': 'Hi', 'all_day': True, 'start': self.BAD, 'end': '2026-10-22', 'remote': 'icloud'})
        r = self.sync()
        self.assertEqual((self.puts(), self.puts('DELETE')), ([], []), 'zero PUT / DELETE')
        self.assertEqual(r['pushed'], 0)
        row = self.ev('e-bad')
        self.assertTrue(row['conflict'])
        self.assertNotIn('rid', row)
        self.assertEqual(self.doc().get('outbox'), [])
        for c in self.fake.calls:
            self.assertNotIn(b'ATTENDEE', c[4] or b'')
        # a linked timed row with a tampered end / rid / etag: no write either, and the pull still works
        self.fake.calls.clear()
        rid = WORK + 'r1.ics'
        self.fake.put(rid, vcal(*vevent('r1', 'SUMMARY:Linked', 'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z')))
        self.ok(self.sync())
        ident = next(e['id'] for e in self.doc()['events'] if e.get('rid') == rid)
        for change in ({'end': '2026-10-20T10:30:00+00:00\r\nATTENDEE:mailto:v@example.com'}, {'rid': WORK + '../x.ics'},
                       {'etag': '"e1"\r\nX-Evil: 1'}, {'rid': 7}):
            d = self.doc()
            row = next(e for e in d['events'] if e['id'] == ident)
            keep = dict(row)
            row.update(change, title='Edited ' + str(len(self.fake.calls)))
            d['outbox'] = [{'op': 'update', 'id': ident, 'remote': 'icloud', 'at': '2026-10-20T00:00:00+00:00', 'tries': 0}]
            jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
            self.sync()
            self.assertEqual(self.puts(), [], change)
            self.assertTrue(self.ev(ident)['conflict'], change)
            d = self.doc()
            d['events'] = [keep if e['id'] == ident else e for e in d['events']]
            jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        # a tampered delete op: nothing sent
        self.tamper({'id': 'gone-1', 'title': 'x', 'start': T['start'], 'end': T['end']}, 'delete',
                    rid=WORK + '%2e%2e/victim.ics', etag='"1"')
        self.sync()
        self.assertEqual(self.puts('DELETE'), [])

    def test_adapter_rebuilds_the_event_itself(self):
        a = dav.CalDAV(self.st, {'server': SERVER, 'origin': 'https://p42-caldav.icloud.com', 'calendar': WORK,
                                 'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'})
        bad = {'title': 'Hi', 'all_day': True, 'start': self.BAD, 'end': '2026-10-22'}
        for call in (lambda: a.create(bad), lambda: a.update(WORK + 'x.ics', '"1"', bad),
                     lambda: a.update(WORK + 'x.ics', '"1"\r\nX: y', cal.event(T)), lambda: a.delete(WORK + 'x.ics', 'a\nb')):
            with self.assertRaises(rm.RemoteError):
                call()
        self.assertEqual(self.fake.calls, [])
        with self.assertRaises(ValueError):
            dav.props(bad)
        day = dav.props(cal.event({'title': 'D', 'all_day': True, 'start': '2026-10-21'}))
        self.assertIn('DTSTART;VALUE=DATE:20261021', day)
        self.assertIn('DTEND;VALUE=DATE:20261022', day)

    def test_rid_must_stay_a_resource_of_the_calendar(self):
        a = dav.CalDAV(self.st, {'server': SERVER, 'origin': 'https://p42-caldav.icloud.com', 'calendar': WORK,
                                 'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'})
        self.assertTrue(a.rid_ok(WORK + 'abc-123.ics'))
        self.assertTrue(a.rid_ok(WORK + 'owner%40example.ics'))
        for bad in ('../x.ics', '%2e%2e/x.ics', '%2E%2e/x.ics', '.%2e/x.ics', './x.ics', '..', 'a/../b.ics', 'sub/x.ics',
                    'x.txt', '.ics', 'x%2fy.ics', 'x%5c..%5cy.ics', 'x%252e.ics', 'x.ics?a', 'x.ics#a', 'a b.ics', 'a\\b.ics'):
            self.assertFalse(a.rid_ok(WORK + bad), bad)
            with self.assertRaises(rm.RemoteError, msg=bad):
                a.delete(WORK + bad, None)
        for bad in ('/1234567/calendars/other/x.ics', '//evil.example' + WORK + 'x.ics', HOME + 'x.ics', 7, None):
            self.assertFalse(a.rid_ok(bad), bad)
        self.assertEqual(self.fake.calls, [])
        for cal_path in ('/a/../b/', '/a/%2e%2e/b/', '//evil.example/a/'):
            with self.assertRaises(ValueError, msg=cal_path):
                dav.CalDAV(self.st, {'server': SERVER, 'calendar': cal_path, 'user_key': 'ICLOUD_USER_SECRET',
                                     'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'})
        # a REPORT href that walks out of the collection is never a row
        self.ok(self.connect())
        self.fake.put(WORK + 'ok.ics', vcal(*vevent('ok', 'SUMMARY:Fine', 'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z')))
        self.fake.put(WORK + 'sub/deep.ics', vcal(*vevent('d', 'SUMMARY:Deep', 'DTSTART:20261020T093000Z', 'DTEND:20261020T103000Z')))
        self.ok(self.sync())
        self.assertEqual(sorted(e['title'] for e in self.doc()['events']), ['Fine'])

    def test_key_namespace_bound_to_its_server(self):
        self.hosts('dav.example.net\n')
        for kw in ({'user_key': 'CALDAV_WORK_USER_SECRET'}, {'pass_key': 'CALDAV_WORK_PASS_SECRET'}):
            self.assertEqual(self.elevate(**kw)['why'], 'bad_key_name', kw)                 # CALDAV_ names to iCloud
        for kw in ({'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'CALDAV_WORK_PASS_SECRET'},
                   {'user_key': 'CALDAV_WORK_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'}):
            self.assertEqual(self.elevate(server='https://dav.example.net/', **kw)['why'], 'bad_key_name', kw)
        self.assertEqual((self.read, self.fake.calls), ([], []))
        # owner lists iCloud too: CALDAV_ names may go there
        self.hosts('dav.example.net\ncaldav.icloud.com\n')
        self.assertTrue(self.elevate(user_key='CALDAV_WORK_USER_SECRET', pass_key='CALDAV_WORK_PASS_SECRET')['ok'])
        # tampered on sync: an ICLOUD_ name pointed at the owner's other server
        self.hosts('DAV.Example.NET   # upper case is fine\n')
        self.assertEqual(dav.owner_hosts(self.st), {'dav.example.net'})
        self.ok(self.connect())
        self.read.clear()
        self.fake.calls.clear()
        d = self.doc()
        d['subscriptions']['icloud'].update(server='https://dav.example.net/', origin='https://dav.example.net')
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        self.assertEqual(rm.host_sync(self.st, 'icloud', authorize=lambda *_: True)['why'], 'bad_key_name')
        self.assertEqual((self.read, self.fake.calls), ([], []))

    def test_connect_rate_gap(self):
        import asyncio
        from types import SimpleNamespace
        t = [100.0]
        host = SimpleNamespace(st=self.st, _calrun=rm.Runner(self.st, clock=lambda: t[0]))
        def go(**kw):
            req = {'t': 'calsync', 'op': 'connect', 'kind': 'caldav', 'name': 'icloud', 'server': SERVER,
                   'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET', **kw}
            return asyncio.run(rm.elevate_request(host, self.st, req))
        self.assertTrue(go()['ok'])
        n = len(self.read)
        t[0] += 29
        self.assertEqual(go(pass_key='ICLOUD_OTHER_SECRET'), {'ok': False, 'name': 'icloud', 'why': 'too_soon'})
        self.assertEqual(len(self.read), n, 'no key read inside the gap')
        self.assertTrue(go(name='other')['ok'])                     # per name
        t[0] += 2
        self.assertTrue(go()['ok'])
        self.assertLessEqual(set(host._calrun.connects), {'icloud', 'other'})
        t[0] += 100
        go(name='third', server='https://attacker.example/')
        self.assertEqual(set(host._calrun.connects), {'third'})     # old entries pruned

    def test_cli_sync_of_unknown_name(self):
        import contextlib
        import io
        from agentj import state
        def run(*args):
            out = io.StringIO()
            with mock.patch.object(state, 'state_dir', return_value=self.root), contextlib.redirect_stdout(out), \
                    mock.patch.dict('os.environ', {'AGENTJ_ELEVATE_SOCK': str(self.root / 'none.sock')}):
                code = jcards.command(['cal', *args, '--json'])
            return code, json.loads(out.getvalue())
        self.assertEqual(run('sync', '--name', 'nosuch'), (1, {'ok': False, 'why': 'not_found'}))
        self.assertEqual(run('sync', '--name', 'Bad Name')[1]['why'], 'bad_name')
        d = self.doc()
        d['subscriptions']['personal'] = {'enabled': False, 'key': 'CALENDAR_ICS_SECRET'}
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        self.assertEqual(run('sync', '--name', 'personal')[1]['why'], 'disabled')


class CLI(Base):
    def test_cli_connect_flags(self):
        import contextlib
        import io
        from agentj import state
        def run(*args):
            out = io.StringIO()
            with mock.patch.object(state, 'state_dir', return_value=self.root), contextlib.redirect_stdout(out), \
                    mock.patch.dict('os.environ', {'AGENTJ_ELEVATE_SOCK': str(self.root / 'none.sock')}):
                code = jcards.command(['cal', *args, '--json'])
            return code, json.loads(out.getvalue())
        code, r = run('connect', '--kind', 'caldav', '--name', 'icloud', '--server', SERVER, '--user-key', 'ICLOUD_USER_SECRET',
                      '--pass-key', 'ICLOUD_APP_PASSWORD_SECRET', '--calendar', 'Work', '--push-new', '--auto')
        self.assertEqual(code, 0, r)
        self.assertTrue(r['sync']['ok'])
        row = cal.listing(self.root)['subscriptions'][0]
        self.assertEqual((row['kind'], row['push_new'], row['auto']), ('caldav', True, True))
        code, r = run('add', '--title', 'From CLI', '--start', '2026-10-20T09:00Z')
        code, r = run('sync', '--name', 'icloud')
        self.assertEqual((code, r['pushed'], r['why']), (1, 0, 'approval_required'))
        code, r = run('connect', '--kind', 'google', '--name', 'g1', '--server', SERVER)
        self.assertEqual(r['why'], 'shape')
        self.assertEqual(run('disconnect', '--name', 'icloud')[0], 0)
        self.assertEqual(self.doc()['subscriptions'], {})


if __name__ == '__main__':
    unittest.main()
