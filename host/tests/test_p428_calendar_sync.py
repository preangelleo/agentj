"""P428 item 2: calendar undo journal, offline outbox, conflicts, two-way sync engine, Google adapter (fake gog), serve frame."""
import _hermetic  # noqa: F401: isolated test HOME/state before importing the host
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock
from agentj import jcards, jcards_cal as cal, jcards_cal_undo as undo, state

T = {'title': 'Meeting', 'start': '2026-10-20T09:30:00+00:00', 'end': '2026-10-20T10:30:00+00:00'}
GOOGLE = {'kind': 'google', 'enabled': True, 'calendar': 'team.cal@group.calendar.google.com', 'push_new': False, 'auto': False}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p428-')
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def write(self, events=(), subs=None, **extra):
        doc = {'schema': 1, 'events': [dict(e) for e in events], 'subscriptions': subs if subs is not None else {'google': dict(GOOGLE)}, **extra}
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', doc)

    def doc(self):
        return cal.load(self.root)[0]

    def linked(self, ident='e-1', rid='gid1', etag='"1"', **kw):
        e = {**cal.event(T | kw), 'id': ident, 'remote': 'google', 'rid': rid, 'etag': etag}
        e['base'] = 'x'
        return e

    def ok(self, res):
        self.assertTrue(res.get('ok'), res)
        return res

    def box(self):
        return [(o['op'], o['id']) for o in self.doc().get('outbox', [])]


class Journal(Base):
    def test_coalescing_rules(self):
        self.write()
        # create + update → create; create + delete → both dropped
        e = self.ok(cal.change(self.root, 'add', to='google', **T))['events'][0]
        self.assertEqual((e['remote'], e['pending']), ('google', True))
        self.ok(cal.change(self.root, 'edit', e['id'], title='Moved'))
        self.assertEqual(self.box(), [('create', e['id'])])
        self.ok(cal.change(self.root, 'delete', e['id']))
        self.assertEqual(self.box(), [])
        # update + update → one update; update + delete → delete (with the remote id kept for the push)
        self.write([self.linked()])
        self.ok(cal.change(self.root, 'edit', 'e-1', title='A'))
        self.ok(cal.change(self.root, 'edit', 'e-1', title='B'))
        self.assertEqual(self.box(), [('update', 'e-1')])
        self.ok(cal.change(self.root, 'delete', 'e-1'))
        self.assertEqual(self.box(), [('delete', 'e-1')])
        self.assertEqual(self.doc()['outbox'][0]['rid'], 'gid1')

    def test_local_and_ics_events_never_queue(self):
        self.write(subs={'personal': {'enabled': True, 'key': 'CALENDAR_ICS_SECRET'}})
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.assertIsNone(e['remote'])
        self.assertEqual(self.doc().get('outbox', []), [])
        self.assertEqual(cal.change(self.root, 'add', to='personal', **T)['why'], 'bad_remote')
        self.assertEqual(cal.change(self.root, 'add', to='nope', **T)['why'], 'bad_remote')

    def test_push_new_links_new_events(self):
        self.write(subs={'google': dict(GOOGLE, push_new=True)})
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.assertEqual(e['remote'], 'google')
        self.assertEqual(self.box(), [('create', e['id'])])

    def test_undo_twice_is_redo_and_history_bounded(self):
        self.write(subs={})
        e = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        r = self.ok(cal.change(self.root, 'edit', e['id'], title='Changed'))
        self.assertEqual(r['undo'], 2)
        r = self.ok(undo.undo(self.root))
        self.assertEqual(r['events'][0]['title'], 'Meeting')
        r = self.ok(undo.undo(self.root))
        self.assertEqual(r['events'][0]['title'], 'Changed')
        r = self.ok(undo.undo(self.root, e['id']))
        self.assertEqual(r['events'][0]['title'], 'Meeting')
        for i in range(25):
            self.ok(cal.change(self.root, 'edit', e['id'], title='T%d' % i))
        self.assertEqual(len(self.doc()['history']), undo.HISTORY)
        self.ok(cal.change(self.root, 'delete', e['id']))
        self.assertEqual(self.ok(undo.undo(self.root))['events'][0]['title'], 'T24')
        self.assertEqual(undo.undo(self.root, 'e-none')['why'], 'nothing_to_undo')
        self.assertEqual(undo.undo(self.root, revision='stale')['why'], 'conflict')

    def test_undo_add_of_unpushed_event_drops_create(self):
        self.write()
        self.ok(cal.change(self.root, 'add', to='google', **T))
        self.assertEqual(self.ok(undo.undo(self.root))['events'], [])
        self.assertEqual(self.box(), [])

    def test_undo_of_pushed_changes_queues_the_inverse(self):
        self.write([self.linked()])
        self.ok(cal.change(self.root, 'edit', 'e-1', title='Pushed'))
        d = self.doc(); d['outbox'] = []; d['events'][0]['etag'] = '"2"'           # the update reached Google
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        r = self.ok(undo.undo(self.root))
        self.assertEqual(r['events'][0]['title'], 'Meeting')
        self.assertEqual(self.box(), [('update', 'e-1')])
        self.assertEqual(self.doc()['events'][0]['etag'], '"2"')                     # current sync state kept
        # a delete that reached Google: undo creates it again (no stale remote id)
        self.ok(cal.change(self.root, 'delete', 'e-1'))
        d = self.doc(); d['outbox'] = []
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        self.ok(undo.undo(self.root))
        self.assertEqual(self.box(), [('create', 'e-1')])
        self.assertNotIn('rid', self.doc()['events'][0])

    def test_undo_of_queued_delete_drops_it(self):
        self.write([self.linked()])
        self.ok(cal.change(self.root, 'delete', 'e-1'))
        self.assertEqual(self.box(), [('delete', 'e-1')])
        self.ok(undo.undo(self.root))
        self.assertEqual(self.box(), [('update', 'e-1')])       # the flush sees base == hash and sends nothing
        self.assertEqual(self.doc()['events'][0]['rid'], 'gid1')

    def test_readonly_instances_refuse_edits_and_undo(self):
        self.write([self.linked(readonly=True) | {'readonly': True}])
        self.assertEqual(cal.change(self.root, 'edit', 'e-1', title='x')['why'], 'readonly')
        self.assertEqual(cal.change(self.root, 'delete', 'e-1')['why'], 'readonly')
        self.assertTrue(cal.listing(self.root)['events'][0]['readonly'])

    def test_resolve_both_ways(self):
        mine = cal.event(T | {'title': 'Mine'})
        theirs = self.linked(title='Theirs') | {'conflict': True}
        conflict = {'id': 'e-1', 'remote': 'google', 'at': 'x', 'local': mine, 'remote_state': 'changed'}
        self.write([theirs], conflicts=[conflict])
        r = cal.listing(self.root)
        self.assertTrue(r['events'][0]['conflict'])
        self.assertEqual(r['conflicts'][0]['local']['title'], 'Mine')
        self.assertEqual(r['subscriptions'][0]['conflicts'], 1)
        r = self.ok(undo.resolve(self.root, 'e-1', 'local'))
        self.assertEqual((r['events'][0]['title'], r['events'][0]['conflict']), ('Mine', False))
        self.assertEqual(self.box(), [('update', 'e-1')])
        self.write([theirs], conflicts=[conflict])
        r = self.ok(undo.resolve(self.root, 'e-1', 'remote'))
        self.assertEqual((r['events'][0]['title'], r['conflicts'], self.box()), ('Theirs', [], []))
        self.assertEqual(undo.resolve(self.root, 'e-1', 'remote')['why'], 'not_found')
        self.assertEqual(undo.resolve(self.root, 'e-1', 'both')['why'], 'bad_keep')

    def test_resolve_deleted_states(self):
        gone = {**cal.event(T | {'title': 'Edited'}), 'id': 'e-1', 'remote': 'google', 'conflict': True}
        c = {'id': 'e-1', 'remote': 'google', 'at': 'x', 'local': cal.event(T | {'title': 'Edited'}), 'remote_state': 'deleted'}
        self.write([gone], conflicts=[c])
        self.ok(undo.resolve(self.root, 'e-1', 'local'))
        self.assertEqual(self.box(), [('create', 'e-1')])
        self.write([gone], conflicts=[c])
        self.assertEqual(self.ok(undo.resolve(self.root, 'e-1', 'remote'))['events'], [])
        # pending delete vs remote change: keep mine = delete again
        back = self.linked(title='Theirs') | {'conflict': True}
        self.write([back], conflicts=[dict(c, local=None, remote_state='changed')])
        self.assertEqual(self.ok(undo.resolve(self.root, 'e-1', 'local'))['events'], [])
        self.assertEqual(self.box(), [('delete', 'e-1')])

    def test_listing_exposes_names_counts_flags_only(self):
        subs = {'google': dict(GOOGLE, last_sync='2026-10-20T09:00:00+00:00', auto=True),
                'personal': {'enabled': True, 'key': 'CALENDAR_ICS_SECRET'},
                'icloud': {'kind': 'caldav', 'enabled': True, 'calendar': 'https://p01-caldav.icloud.com/123/calendars/work/',
                           'server': 'https://caldav.icloud.com/', 'user_key': 'ICLOUD_USER_SECRET', 'pass_key': 'ICLOUD_APP_PASSWORD_SECRET'}}
        self.write([self.linked(rid='Zsecretremoteid', etag='"etag-xyz"')], subs=subs)
        self.ok(cal.change(self.root, 'edit', 'e-1', title='Queued'))
        r = cal.listing(self.root)
        text = json.dumps(r)
        for needle in ('CALENDAR_ICS_SECRET', 'ICLOUD_USER_SECRET', 'ICLOUD_APP_PASSWORD_SECRET', 'icloud.com', 'Zsecretremoteid',
                       'etag-xyz', 'group.calendar.google.com', 'rid', 'base'):
            self.assertNotIn(needle, text)
        ev = r['events'][0]
        self.assertEqual((ev['remote'], ev['pending'], ev['conflict']), ('google', True, False))
        rows = {s['name']: s for s in r['subscriptions']}
        self.assertEqual(rows['google'], {'name': 'google', 'kind': 'google', 'enabled': True, 'last_sync': '2026-10-20T09:00:00+00:00',
                                          'last_error': None, 'queued': 1, 'conflicts': 0, 'push_new': False, 'auto': True})
        self.assertEqual((rows['personal']['kind'], rows['icloud']['kind']), ('ics', 'caldav'))
        q = undo.queue_view(self.root)
        self.assertEqual((q['queued'], q['outbox'][0]['op']), (1, 'update'))
        self.assertNotIn('Zsecretremoteid', json.dumps(q))

    def test_cli_undo_resolve_queue_to(self):
        self.write()
        def run(*args):
            out = io.StringIO()
            with mock.patch.object(state, 'state_dir', return_value=self.root), contextlib.redirect_stdout(out):
                code = jcards.command(['cal', *args, '--json'])
            return code, json.loads(out.getvalue())
        code, r = run('add', '--title=-rf', '--start', '2026-10-20', '--all-day', '--to', 'google')
        self.assertEqual((code, r['events'][0]['title'], r['events'][0]['remote']), (0, '-rf', 'google'))
        self.assertEqual(run('queue')[1]['queued'], 1)
        self.assertEqual(run('undo')[1]['events'], [])
        self.assertEqual(run('undo')[0], 0)
        self.assertEqual(run('resolve', '--entry', 'e-x', '--keep', 'local'), (1, {'ok': False, 'why': 'not_found'}))

    def test_outbox_bounded(self):
        full = [{'op': 'update', 'id': 'o%d' % i, 'remote': 'google', 'at': 'x', 'tries': 0} for i in range(undo.OUTBOX)]
        self.write([self.linked()], outbox=full)
        self.assertEqual(cal.change(self.root, 'edit', 'e-1', title='y')['why'], 'full')
        self.assertEqual(self.doc()['events'][0]['title'], 'Meeting')


# ---------------------------------------------------------------- item 2b: the sync engine, Google adapter, host plumbing
import asyncio  # noqa: E402
import datetime as dt  # noqa: E402
import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from agentj import jcards_cal_remote as rm  # noqa: E402

NOW = dt.datetime(2026, 10, 20, 9, tzinfo=dt.timezone.utc)


class Fake:
    """A remote calendar in memory: rows by remote id, etags counting up, failures injectable per call."""
    kind = 'google'

    def __init__(self):
        self.rows, self.calls, self.n, self.fail, self.auth, self.fields = {}, [], 0, {}, 0, []

    def put(self, rid, **kw):
        old = self.rows.get(rid, {})
        ev = cal.event({**T, **{k: v for k, v in old.items() if k in cal.FIELDS}, **kw})
        self.n += 1
        self.rows[rid] = {**ev, 'rid': rid, 'etag': '"e%d"' % self.n, **({'readonly': True} if kw.get('readonly') else {})}

    def _maybe(self, op):
        self.calls.append(op)
        if self.fail.get(op):
            raise rm.RemoteError(self.fail[op])

    def list(self, lo, hi):
        self._maybe('list')
        return [dict(r) for r in self.rows.values()]

    def create(self, ev):
        self._maybe('create')
        rid = 'g%d' % (len(self.rows) + 100)
        self.put(rid, **{k: ev[k] for k in cal.FIELDS if k in ev})
        return {'rid': rid, 'etag': self.rows[rid]['etag']}

    def update(self, rid, etag, ev, fields=None):
        self._maybe('update')
        self.fields.append(fields)
        if rid not in self.rows:
            raise rm.RemoteError('gone')
        self.put(rid, **{k: ev[k] for k in cal.FIELDS if k in ev and (fields is None or k in fields)})
        return {'etag': self.rows[rid]['etag']}

    def delete(self, rid, etag):
        self._maybe('delete')
        if self.rows.pop(rid, None) is None:
            raise rm.RemoteError('gone')

    def auth_failed(self):
        self.auth += 1


class Engine(Base):
    def setUp(self):
        super().setUp()
        self.remote = Fake()
        self.write()

    def sync(self):
        return rm.sync(self.root, 'google', self.remote, NOW)

    def ev(self, ident='e-1'):
        return next((e for e in self.doc()['events'] if e['id'] == ident), None)

    def pushed_event(self, **kw):
        e = self.ok(cal.change(self.root, 'add', to='google', **(T | kw)))['events'][0]
        r = self.ok(self.sync())
        self.assertEqual(r['pushed'], 1)
        return e['id'], self.ev(e['id'])['rid']

    def test_create_push_then_idle(self):
        ident, rid = self.pushed_event()
        self.assertEqual(self.remote.rows[rid]['title'], 'Meeting')
        r = self.ok(self.sync())
        self.assertEqual({k: r[k] for k in ('pulled', 'pushed', 'queued', 'conflicts', 'linked_duplicates')},
                         {'pulled': 1, 'pushed': 0, 'queued': 0, 'conflicts': 0, 'linked_duplicates': 0})
        self.assertEqual(set(r), {'ok', 'name', 'pulled', 'pushed', 'queued', 'conflicts', 'linked_duplicates'})
        self.assertEqual(self.remote.calls.count('create'), 1)
        self.assertIsNotNone(cal.listing(self.root)['subscriptions'][0]['last_sync'])

    def test_three_way_matrix(self):
        ident, rid = self.pushed_event()
        # only remote changed → overwrite
        self.remote.put(rid, title='Remote edit')
        self.ok(self.sync())
        self.assertEqual(self.ev(ident)['title'], 'Remote edit')
        # only local changed → pushed
        self.ok(cal.change(self.root, 'edit', ident, title='Local edit'))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        self.assertEqual(self.remote.rows[rid]['title'], 'Local edit')
        # both changed, equal → rebase, nothing pushed, no conflict
        self.ok(cal.change(self.root, 'edit', ident, title='Same'))
        self.remote.put(rid, title='Same')
        r = self.ok(self.sync())
        self.assertEqual((r['pushed'], r['conflicts'], self.ev(ident)['etag']), (0, 0, self.remote.rows[rid]['etag']))
        # both changed, different → remote wins in the card, local saved, its op dropped
        self.ok(cal.change(self.root, 'edit', ident, title='Mine'))
        self.remote.put(rid, title='Theirs')
        r = self.ok(self.sync())
        self.assertEqual((r['conflicts'], r['queued'], r['pushed']), (1, 0, 0))
        self.assertEqual(self.ev(ident)['title'], 'Theirs')
        row = cal.listing(self.root)
        self.assertTrue(row['events'][0]['conflict'])
        self.assertEqual(row['conflicts'][0]['local']['title'], 'Mine')
        # keep mine → pushed on the next sync
        self.ok(undo.resolve(self.root, ident, 'local'))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        self.assertEqual(self.remote.rows[rid]['title'], 'Mine')

    def test_remote_deleted_with_and_without_local_change(self):
        ident, rid = self.pushed_event()
        del self.remote.rows[rid]
        self.ok(self.sync())
        self.assertIsNone(self.ev(ident))
        ident, rid = self.pushed_event(title='Second')
        self.ok(cal.change(self.root, 'edit', ident, title='Edited'))
        del self.remote.rows[rid]
        r = self.ok(self.sync())
        self.assertEqual((r['conflicts'], r['queued'], self.ev(ident)['title']), (1, 0, 'Edited'))
        self.assertEqual(self.doc()['conflicts'][0]['remote_state'], 'deleted')
        self.assertNotIn('rid', self.ev(ident))
        self.ok(undo.resolve(self.root, ident, 'local'))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        self.assertEqual([r['title'] for r in self.remote.rows.values()], ['Edited'])

    def test_pending_delete_vs_remote_change_and_plain_delete(self):
        ident, rid = self.pushed_event()
        self.ok(cal.change(self.root, 'delete', ident))
        self.remote.put(rid, title='Changed there')
        r = self.ok(self.sync())
        self.assertEqual((r['conflicts'], self.ev(ident)['title']), (1, 'Changed there'))
        self.assertIsNone(self.doc()['conflicts'][0]['local'])
        self.assertIn(rid, self.remote.rows)
        self.ok(undo.resolve(self.root, ident, 'local'))           # keep mine = delete it
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        self.assertEqual(self.remote.rows, {})

    def test_outside_window_untouched(self):
        ident, rid = self.pushed_event(start='2025-01-01T09:00:00Z', end='2025-01-01T10:00:00Z')
        self.remote.rows.clear()                                   # the window does not reach back to it
        self.ok(self.sync())
        self.assertIsNotNone(self.ev(ident))

    def test_pull_new_recurring_readonly_and_duplicates(self):
        local = self.ok(cal.change(self.root, 'add', **T))['events'][0]
        self.remote.put('dup', title='  meeting ')                # same instants, title casefolded + stripped
        self.remote.put('new', title='Lunch', start='2026-10-21T12:00:00+09:00', end='2026-10-21T13:00:00+09:00')
        self.remote.put('rec_20261022', title='Standup', readonly=True)
        r = self.ok(self.sync())
        self.assertEqual((r['pulled'], r['linked_duplicates']), (3, 1))
        rows = cal.listing(self.root)['events']
        self.assertEqual(len(rows), 3)
        mine = next(e for e in rows if e['id'] == local['id'])
        self.assertEqual((mine['remote'], mine['title']), ('google', 'meeting'))   # P428 F5: the card takes the remote values
        self.assertEqual((r['conflicts'], r['queued'], self.remote.calls.count('update')), (0, 0, 0))   # only the case differed
        self.assertTrue(next(e for e in rows if e['title'] == 'Standup')['readonly'])
        rec = next(e for e in rows if e['title'] == 'Standup')['id']
        self.assertEqual(cal.change(self.root, 'edit', rec, title='x')['why'], 'readonly')
        self.assertEqual(self.ok(self.sync())['linked_duplicates'], 0)
        self.assertEqual(len(cal.listing(self.root)['events']), 3)

    def test_same_remote_id_twice_and_other_remote_kept(self):
        d = self.doc()
        d['subscriptions']['work'] = dict(GOOGLE)
        d['events'].append({**cal.event(T), 'id': 'w-1', 'remote': 'work', 'rid': 'w1', 'etag': '"w"'})
        jcards._write_json(self.root, ('cards-data', 'calendar'), 'calendar.json', d)
        self.remote.put('x1')
        twice = Fake()
        twice.list = lambda lo, hi: [dict(self.remote.rows['x1']), dict(self.remote.rows['x1'], title='Other')]
        r = self.ok(rm.sync(self.root, 'google', twice, NOW))
        self.assertEqual((r['pulled'], r['linked_duplicates']), (1, 0))
        self.assertEqual(sorted(e['remote'] for e in self.doc()['events']), ['google', 'work'])

    def test_offline_keeps_queue_and_counts_tries(self):
        self.ok(cal.change(self.root, 'add', to='google', **T))
        self.remote.fail['create'] = 'offline'
        r = self.sync()
        self.assertEqual((r['ok'], r['offline'], r['why'], r['queued']), (False, True, 'offline', 1))
        self.assertEqual(self.doc()['outbox'][0]['tries'], 1)
        self.remote.fail = {'list': 'offline'}
        r = self.sync()
        self.assertEqual((r['offline'], self.doc()['outbox'][0]['tries']), (True, 2))
        self.assertEqual(cal.listing(self.root)['subscriptions'][0]['last_error'], 'offline')
        self.remote.fail = {}
        r = self.ok(self.sync())
        self.assertEqual((r['pushed'], r['queued']), (1, 0))
        self.assertIsNone(cal.listing(self.root)['subscriptions'][0]['last_error'])

    def test_auth_failure_keeps_queue(self):
        self.ok(cal.change(self.root, 'add', to='google', **T))
        self.remote.fail['list'] = 'auth'
        r = self.sync()
        self.assertEqual((r['ok'], r['why'], r['queued'], self.remote.auth), (False, 'auth', 1, 1))
        self.assertNotIn('offline', r)

    def test_cap_refuses_atomically(self):
        self.write([dict(cal.event(T), id='e%d' % i) for i in range(cal.MAX_EVENTS - 1)])
        self.remote.put('a', title='A')
        self.remote.put('b', title='B')
        before = cal.load(self.root)[0]['events']
        r = self.sync()
        self.assertEqual((r['ok'], r['why'], r['pulled']), (False, 'full', 2))
        self.assertEqual(cal.load(self.root)[0]['events'], before)

    def test_concurrent_owner_edit_retries_once(self):
        self.remote.put('x1', title='Remote')
        inner = self.remote.list
        def racing(lo, hi):
            self.ok(cal.change(self.root, 'add', **(T | {'title': 'Concurrent'})))
            return inner(lo, hi)
        self.remote.list = racing
        self.ok(self.sync())
        self.assertEqual(sorted(e['title'] for e in self.doc()['events']), ['Concurrent', 'Remote'])

    def test_update_on_gone_remote_becomes_conflict(self):
        ident, rid = self.pushed_event()
        self.ok(cal.change(self.root, 'edit', ident, title='Late'))
        del self.remote.rows[rid]
        self.remote.list = lambda lo, hi: [{'rid': rid, 'ok': False}]
        r = self.ok(self.sync())                                   # unreadable row with that id: kept, update → gone
        self.assertEqual(r['conflicts'], 1)
        self.assertEqual(self.doc()['conflicts'][0]['remote_state'], 'deleted')

    def test_disconnect_keeps_local_copies(self):
        ident, rid = self.pushed_event()
        self.remote.put('rec', readonly=True, title='Series')
        self.ok(self.sync())
        self.ok(cal.change(self.root, 'edit', ident, title='Queued'))
        self.ok(rm.disconnect(self.root, 'google'))
        d = self.doc()
        self.assertEqual(([e['title'] for e in d['events']], d['outbox'], d['subscriptions']), (['Queued'], [], {}))
        self.assertNotIn('rid', d['events'][0])
        self.assertEqual(rm.disconnect(self.root, 'google')['why'], 'not_found')


FAKEGOG = r'''#!{py} -IS
import json, pathlib, sys
d = pathlib.Path(__file__).resolve().parent
args = sys.argv[1:]
with open(d / "calls.jsonl", "a") as f:
    f.write(json.dumps(args) + "\n")
mode = json.loads((d / "mode.json").read_text())
sub = args[args.index("calendar") + 1]
code, body = mode.get(sub, [0, {{}}])
print(json.dumps(body))
sys.exit(code)
'''


class GoogleAdapter(Base):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        from test_l1 import _state
        from agentj import google, google_connect as gc
        self.google, self.gc = google, gc
        self.st = _state(self.root / 'h')
        self.root = self.st.root
        self.write()
        self.fake = self.root.parent / 'fakegog'
        self.fake.mkdir()
        exe = self.fake / 'gog'
        exe.write_text(FAKEGOG.format(py=sys.executable))
        exe.chmod(0o755)
        for p in (mock.patch.object(google, 'binary', return_value=exe), mock.patch.object(google, 'installed', return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def mode(self, **kw):
        (self.fake / 'mode.json').write_text(json.dumps(kw))

    def calls(self):
        f = self.fake / 'calls.jsonl'
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []

    def test_argv_shapes_and_tolerant_parse(self):
        items = [{'id': 'abc123', 'etag': '"1"', 'summary': 'From Google', 'description': 'n',
                  'start': {'dateTime': '2026-10-20T18:00:00+09:00'}, 'end': {'dateTime': '2026-10-20T19:00:00+09:00'},
                  'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 15}]}},
                 {'id': 'day1', 'etag': '"2"', 'summary': 'Holiday', 'start': {'date': '2026-10-22'}, 'end': {'date': '2026-10-23'}},
                 {'id': 'inst_20261024T010000Z', 'etag': '"3"', 'summary': 'Weekly', 'recurringEventId': 'inst',
                  'start': {'dateTime': '2026-10-24T01:00:00Z'}, 'end': {'dateTime': '2026-10-24T02:00:00Z'}},
                 {'id': 'gone1', 'status': 'cancelled'}, {'id': '-flag', 'summary': 'x'}, 'junk']
        self.mode(events=[0, {'events': items, 'nextPageToken': ''}], create=[0, {'event': {'id': 'new1', 'etag': '"9"'}}],
                  update=[0, {'id': 'abc123', 'etag': '"4"'}], delete=[0, {}])
        self.ok(cal.change(self.root, 'add', to='google', **(T | {'title': '--rf', 'note': '-y --all-pages', 'reminder': 30})))
        adapter = rm.adapter_for(self.st, self.doc()['subscriptions']['google'])
        r = self.ok(rm.sync(self.root, 'google', adapter, NOW))
        self.assertEqual((r['pulled'], r['pushed']), (3, 1))
        rows = {e['title']: e for e in cal.listing(self.root)['events']}
        self.assertEqual(rows['From Google']['reminder'], 15)
        self.assertTrue(rows['Holiday']['all_day'])
        self.assertTrue(rows['Weekly']['readonly'])
        calls = self.calls()
        pre = ['-a', 'auto', '--json', '--no-input', '--enable-commands-exact', self.gc.CAL_COMMANDS]
        for c in calls:
            self.assertEqual(c[:6], pre)
        ls, mk = calls[0][6:], calls[1][6:]
        self.assertEqual(ls[:3], ['calendar', 'events', GOOGLE['calendar']])
        self.assertEqual(ls[5:], ['--all-pages', '--max', '250'])
        self.assertTrue(ls[3].startswith('--from=2026-09-20') and ls[4].startswith('--to=2027-10-20'))
        self.assertEqual(mk[:3], ['calendar', 'create', GOOGLE['calendar']])
        self.assertIn('--summary=--rf', mk)
        self.assertIn('--description=-y --all-pages', mk)
        self.assertIn('--reminder=popup:30m', mk)
        self.assertIn('--from=2026-10-20T09:30:00+00:00', mk)
        for a in mk[3:]:                                           # every user value is bound with '=' to its flag
            self.assertTrue(a.startswith('--'), a)
        # edit a pulled event → update with --send-updates=none; delete → -y
        ident = rows['From Google']['id']
        self.ok(cal.change(self.root, 'edit', ident, title='Edited', reminder=None))
        self.ok(cal.change(self.root, 'delete', rows['Holiday']['id']))
        self.mode(events=[0, {'items': items[:2]}], update=[0, {'id': 'abc123', 'etag': '"4"'}], delete=[0, {}])
        self.ok(rm.sync(self.root, 'google', adapter, NOW))
        tail = [c[6:] for c in self.calls()[2:]]
        up = next(c for c in tail if c[1] == 'update')
        self.assertEqual(up[2:4], [GOOGLE['calendar'], 'abc123'])
        self.assertIn('--no-reminders', up)
        self.assertIn('--send-updates=none', up)
        dl = next(c for c in tail if c[1] == 'delete')
        self.assertEqual(dl[2:], [GOOGLE['calendar'], 'day1', '-y', '--send-updates=none'])

    def test_error_words(self):
        cases = [(4, {}, 'auth'), (1, {'error': {'message': 'oauth2: "invalid_grant"'}}, 'auth'),
                 (2, {'error': {'message': 'missing --account (or set GOG_ACCOUNT)'}}, 'auth'),
                 (8, {}, 'offline'), (-2, None, 'offline'), (1, {'error': {'message': 'dial tcp: lookup www.googleapis.com: no such host'}}, 'offline'),
                 (1, {'error': {'code': 412, 'message': 'Precondition Failed'}}, 'conflict'), (5, {}, 'gone'),
                 (1, {'error': {'message': 'googleapi: Error 410: Resource has been deleted'}}, 'gone'), (1, {'error': {}}, 'failed'), (-1, None, 'failed')]
        for code, body, word in cases:
            self.assertEqual(rm.classify(code, body), word, (code, body))

    def test_auth_failure_resets_connection_step(self):
        self.gc._set(self.st, 'calendar-write', 'ready')
        self.mode(events=[4, {'error': {'class': 'auth', 'code': 4, 'message': 'token expired for owner@example.com'}}])
        r = rm.host_sync(self.st, 'google')
        self.assertEqual((r['ok'], r['why']), (False, 'auth'))
        self.assertEqual(self.google.read(self.st)['connections']['calendar-write']['step'], 'oauth_consent_required')
        self.assertNotIn('example.com', json.dumps(r) + json.dumps(self.doc()))

    def test_connect_requires_ready_google(self):
        self.write(subs={})
        r = rm.connect(self.st, 'google', 'google')
        self.assertEqual((r['why'], r['hint']), ('google_not_connected', 'agentj google connect --purpose calendar-write'))
        self.gc._set(self.st, 'calendar-write', 'ready')
        self.mode(events=[0, []])
        for bad, why in ((('caldav', 'icloud'), 'bad_server'), (('ics', 'x1'), 'bad_kind'), (('google', 'Bad'), 'bad_name')):
            self.assertEqual(rm.connect(self.st, *bad)['why'], why)
        self.assertEqual(rm.connect(self.st, 'google', 'google', calendar='-x')['why'], 'bad_calendar')
        r = rm.host_op(self.st, {'op': 'connect', 'kind': 'google', 'name': 'google', 'push_new': True, 'auto': True})
        self.assertTrue(r['ok'] and r['sync']['ok'], r)
        row = cal.listing(self.root)['subscriptions'][0]
        self.assertEqual((row['kind'], row['push_new'], row['auto']), ('google', True, True))

    def test_elevate_socket_routes_calsync(self):
        from test_l1 import _host
        from agentj import elevate
        host = _host(self.st, [])
        self.mode(events=[0, {'events': []}])
        path = str(self.root.parent / 'e.sock')
        async def go():
            srv = await asyncio.start_unix_server(host.elevate.on_client, path=path)
            try:
                bad = await asyncio.to_thread(elevate.client_request, self.st, {'t': 'calsync', 'op': 'sync', 'name': 'google', 'x': 1}, 30, None, path)
                good = await asyncio.to_thread(elevate.client_request, self.st, {'t': 'calsync', 'op': 'sync', 'name': 'google'}, 30, None, path)
            finally:
                srv.close()
            return bad, good
        bad, good = asyncio.run(go())
        self.assertEqual(bad['why'], 'shape')
        self.assertTrue(good['ok'], good)
        self.assertEqual(set(good), {'ok', 'name', 'pulled', 'pushed', 'queued', 'conflicts', 'linked_duplicates'})

    def test_elevate_shapes(self):
        host = SimpleNamespace(st=self.st)
        async def go():
            for bad in ({'t': 'calsync'}, {'t': 'calsync', 'op': 'sync'}, {'t': 'calsync', 'op': 'sync', 'name': '-x'},
                        {'t': 'calsync', 'op': 'sync', 'name': 'google', 'kind': 'google'},
                        {'t': 'calsync', 'op': 'connect', 'name': 'google', 'kind': 'google', 'push_new': 'yes'},
                        {'t': 'calsync', 'op': 'connect', 'name': 'google', 'kind': 'ics'},
                        {'t': 'calsync', 'op': 'connect', 'name': 'google', 'kind': 'google', 'server': 'https://x'},
                        {'t': 'calsync', 'op': 'drop', 'name': 'google'}):
                r = await rm.elevate_request(host, self.st, bad)
                self.assertEqual((r['ok'], r['why']), (False, 'shape'), bad)
            self.mode(events=[0, {'events': []}])
            r = await rm.elevate_request(host, self.st, {'t': 'calsync', 'op': 'sync', 'name': 'google'})
            self.assertTrue(r['ok'], r)
        asyncio.run(go())

    def test_cli_falls_back_in_process(self):
        self.gc._set(self.st, 'calendar-write', 'ready')
        self.mode(events=[0, {'events': []}])
        def run(*args):
            out = io.StringIO()
            with mock.patch.object(state, 'state_dir', return_value=self.root), contextlib.redirect_stdout(out), \
                    mock.patch.dict('os.environ', {'AGENTJ_ELEVATE_SOCK': str(self.root / 'none.sock')}):
                code = jcards.command(['cal', *args, '--json'])
            return code, json.loads(out.getvalue())
        self.write(subs={})
        code, r = run('connect', '--kind', 'google', '--name', 'google', '--push-new')
        self.assertEqual(code, 0, r)
        code, r = run('add', '--title', 'Pushed', '--start', '2026-10-20T09:00Z')
        self.assertEqual(r['events'][0]['remote'], 'google')
        self.mode(events=[0, {'events': []}], create=[0, {'id': 'n1', 'etag': '"1"'}])
        code, r = run('sync', '--name', 'google')
        self.assertEqual((code, r['pushed']), (0, 1))
        self.assertEqual(run('disconnect', '--name', 'google')[0], 0)


class SecurityFixes(Base):
    """P428 security review: F5 per-field push / lossy text / duplicate conflicts, F10 fixed words (Engine's helpers)."""
    sync, ev, pushed_event = Engine.sync, Engine.ev, Engine.pushed_event

    def setUp(self):
        super().setUp()
        self.remote = Fake()
        self.write()

    def test_per_field_push_sends_only_changed_fields(self):
        ident, rid = self.pushed_event(note='agenda', reminder=10)
        self.assertEqual(set(self.ev(ident)['basef']), set(rm.SYNCED))
        self.remote.put(rid, note='their longer note')             # an owner edit there
        self.ok(self.sync())
        self.ok(cal.change(self.root, 'edit', ident, title='Moved'))
        self.assertEqual(self.ok(self.sync())['pushed'], 1)
        self.assertEqual(self.remote.fields[-1], ['title'])
        self.assertEqual((self.remote.rows[rid]['title'], self.remote.rows[rid]['note']), ('Moved', 'their longer note'))
        self.ok(cal.change(self.root, 'edit', ident, start='2026-10-20T11:00:00+00:00', end='2026-10-20T12:00:00+00:00',
                           reminder=None))
        self.ok(self.sync())
        self.assertEqual(self.remote.fields[-1], ['start', 'end', 'reminder'])
        self.assertEqual(self.ok(self.sync())['pushed'], 0)

    def test_old_rows_without_basef_still_push_everything(self):
        self.write([self.linked()])
        self.remote.rows['gid1'] = {**cal.event(T), 'rid': 'gid1', 'etag': '"1"'}
        self.ok(cal.change(self.root, 'edit', 'e-1', title='Edited'))
        self.ok(self.sync())
        self.assertEqual(self.remote.fields, [None])
        self.assertEqual(self.remote.rows['gid1']['title'], 'Edited')
        self.assertIn('basef', self.ev('e-1'))

    def test_lossy_remote_text_is_never_written_back(self):
        self.remote.put('cut', title='Long', note='x' * 1000)
        orig = self.remote.list
        self.remote.list = lambda lo, hi: [dict(r, lossy=['note']) if r['rid'] == 'cut' else r for r in orig(lo, hi)]
        self.ok(self.sync())
        ident = next(e['id'] for e in self.doc()['events'] if e.get('rid') == 'cut')
        self.assertEqual(self.ev(ident)['lossy'], ['note'])
        self.remote.rows['cut']['note'] = 'the full text the server really has'
        self.ok(cal.change(self.root, 'edit', ident, title='Retitled'))
        self.ok(self.sync())
        self.assertEqual(self.remote.fields[-1], ['title'])
        self.assertEqual(self.remote.rows['cut']['note'], 'the full text the server really has')
        # both sides changed → conflict keeps the lossy mark; "keep mine" still never sends the cut note
        self.remote.list = orig
        self.ok(self.sync())
        self.remote.list = lambda lo, hi: [dict(r, lossy=['note']) if r['rid'] == 'cut' else r for r in orig(lo, hi)]
        self.ok(self.sync())
        self.ok(cal.change(self.root, 'edit', ident, title='Mine'))
        self.remote.put('cut', title='Theirs', note='y' * 1000)
        r = self.ok(self.sync())
        self.assertEqual(r['conflicts'], 1)
        self.assertEqual(self.doc()['conflicts'][0]['lossy'], ['note'])
        self.ok(undo.resolve(self.root, ident, 'local'))
        self.ok(self.sync())
        self.assertEqual(self.remote.fields[-1], ['title'])
        self.assertEqual((self.remote.rows['cut']['title'], self.remote.rows['cut']['note']), ('Mine', 'y' * 1000))
        # the owner rewrites the note: theirs now, sent
        self.ok(cal.change(self.root, 'edit', ident, note='short'))
        self.assertNotIn('lossy', self.ev(ident))
        self.ok(self.sync())
        self.assertIn('note', self.remote.fields[-1])
        self.assertEqual(self.remote.rows['cut']['note'], 'short')

    def test_adapters_mark_redacted_or_truncated_text_lossy(self):
        it = {'id': 'g1', 'etag': '"1"', 'summary': 'Plan', 'description': 'd' * 1200,
              'start': {'dateTime': '2026-10-20T09:00:00Z'}, 'end': {'dateTime': '2026-10-20T10:00:00Z'}}
        self.assertEqual(rm.google_item(it)['lossy'], ['note'])
        self.assertNotIn('lossy', rm.google_item({**it, 'description': ' fine '}))
        self.assertEqual(rm.google_item({**it, 'summary': 'A\u2028B', 'description': ''})['lossy'], ['title'])
        self.assertEqual(rm.google_item({**it, 'summary': 'A\u2028B'})['title'], 'A\nB')
        self.assertEqual(rm.google_item({**it, 'summary': ''})['lossy'], ['title', 'note'])

    def test_google_flags_only_for_changed_fields(self):
        ev = {**cal.event(T | {'note': 'n', 'reminder': None}), 'rdefault': True}
        self.assertEqual(rm.Google._flags(ev, False, ['title']), ['--summary=Meeting'])
        self.assertEqual(rm.Google._flags(ev, False, ['note']), ['--description=n'])
        self.assertEqual(rm.Google._flags(ev, False, ['reminder']), [])          # default reminders there stay
        self.assertEqual(rm.Google._flags({**ev, 'rdefault': False}, False, ['reminder']), ['--no-reminders'])
        self.assertEqual(rm.Google._flags(ev, False, ['end']), ['--from=2026-10-20T09:30:00+00:00', '--to=2026-10-20T10:30:00+00:00'])
        self.assertEqual(len(rm.Google._flags(ev, False)), 4)                    # no per-field base: everything, as before

    def test_duplicate_with_a_different_note_becomes_a_conflict(self):
        local = self.ok(cal.change(self.root, 'add', **(T | {'note': 'my agenda', 'reminder': 5})))['events'][0]
        self.remote.put('dup', note='their agenda')
        r = self.ok(self.sync())
        self.assertEqual((r['linked_duplicates'], r['conflicts'], r['pushed']), (1, 1, 0))
        row = self.ev(local['id'])
        self.assertEqual((row['rid'], row['note'], row['reminder'], row['conflict']), ('dup', 'their agenda', None, True))
        c = self.doc()['conflicts'][0]
        self.assertEqual((c['remote_state'], c['local']['note'], c['local']['reminder']), ('changed', 'my agenda', 5))
        self.assertNotIn('update', self.remote.calls)
        self.assertEqual(self.remote.rows['dup']['note'], 'their agenda')

    def test_fixed_words_only(self):
        from types import SimpleNamespace
        st = SimpleNamespace(root=self.root)
        for exc in (ValueError('a path with the owner name in it'), KeyError('x'), RuntimeError('boom')):
            with mock.patch.object(rm, 'adapter_for', side_effect=exc):
                self.assertEqual(rm.host_sync(st, 'google')['why'], 'failed', exc)
        with mock.patch.object(rm, 'adapter_for', side_effect=ValueError('server_not_allowed')):
            self.assertEqual(rm.host_sync(st, 'google')['why'], 'server_not_allowed')
        self.assertEqual(rm.fixed('too_soon'), 'too_soon')
        self.assertEqual(rm.fixed('Traceback: …'), 'failed')


class SecurityRound2(Base):
    """N1: calendar.json is Agent-writable — a tampered row never reaches gog; the argv is built from a rebuilt event."""
    BAD = '2026-10-21\r\nATTENDEE;RSVP=TRUE:mailto:v@example.com'

    def setUp(self):
        super().setUp()
        self.argv = []
        def run(argv, timeout):
            self.argv.append(argv)
            if argv[:2] == ['calendar', 'events']:
                return 0, {'events': [{'id': 'gid1', 'etag': '"1"', 'summary': 'Fine', 'start': {'dateTime': T['start']},
                                       'end': {'dateTime': T['end']}}]}
            return 0, {'id': 'new1', 'etag': '"9"'}
        self.g = rm.Google(SimpleNamespace(root=self.root), GOOGLE, run=run)

    def test_tampered_row_never_reaches_gog(self):
        self.write([{'id': 'e-bad', 'title': 'Hi', 'all_day': True, 'start': self.BAD, 'end': '2026-10-22', 'remote': 'google'},
                    {**cal.event(T | {'title': 'Fine'}), 'id': 'e-ok', 'remote': 'google', 'rid': 'gid1', 'etag': '"1"',
                     'base': 'x', 'end': T['end'] + '\r\nX: y'}],
                   outbox=[{'op': 'create', 'id': 'e-bad', 'remote': 'google', 'at': '2026-10-20T00:00:00+00:00', 'tries': 0},
                           {'op': 'update', 'id': 'e-ok', 'remote': 'google', 'at': '2026-10-20T00:00:00+00:00', 'tries': 0},
                           {'op': 'delete', 'id': 'e-x', 'remote': 'google', 'at': '2026-10-20T00:00:00+00:00', 'tries': 0,
                            'rid': '-y --all', 'etag': None}])
        r = rm.sync(self.root, 'google', self.g, NOW)
        self.assertEqual([a[:2] for a in self.argv], [['calendar', 'events']], 'only the pull: zero create / update / delete')
        self.assertEqual((r['pushed'], r['queued']), (0, 0))
        self.assertTrue(all(e['conflict'] for e in self.doc()['events']))
        self.assertNotIn('ATTENDEE', json.dumps(self.argv))

    def test_adapter_validates_before_building_argv(self):
        for ev in ({'title': 'Hi', 'all_day': True, 'start': self.BAD, 'end': '2026-10-22'},
                   {**cal.event(T), 'title': 'a\rb'}, {**cal.event(T), 'start': '2026-10-20T09:30:00+00:00\nX'}):
            with self.assertRaises(rm.RemoteError):
                self.g.create(ev)
            with self.assertRaises(rm.RemoteError):
                self.g.update('gid1', None, ev)
        self.assertEqual(self.argv, [])
        mk = self.g.create(cal.event({'title': 'D', 'all_day': True, 'start': '2026-10-21'}))
        self.assertEqual(mk['rid'], 'new1')
        self.assertIn('--from=2026-10-21', self.argv[-1])
        self.assertEqual(rm.tag('"ok"'), '"ok"')
        self.assertEqual(rm.tag('"a"\r\nX: y'), '')


class RunnerBounds(Base):
    def setUp(self):
        super().setUp()
        self.write()
        self.t = [1000.0]
        self.runs = []
        self.r = rm.Runner(SimpleNamespace(root=self.root), run=lambda n: (self.runs.append(n), {'ok': True, 'name': n,
                           'pulled': 1, 'pushed': 0, 'queued': 0, 'conflicts': 0, 'linked_duplicates': 0})[1],
                           clock=lambda: self.t[0])

    def test_min_interval_for_phone_and_elevate_syncs(self):
        async def go():
            a = await self.r.now('google', gap=True)
            b = await self.r.now('google', gap=True)
            self.t[0] += 29
            c = await self.r.now('google', gap=True)
            self.t[0] += 2
            d = await self.r.now('google', gap=True)
            e = await self.r.now('google')                        # the runner's own timers are not throttled
            return a, b, c, d, e
        a, b, c, d, e = asyncio.run(go())
        self.assertTrue(a['ok'] and d['ok'] and e['ok'])
        self.assertEqual((b['ok'], b['why'], b['pulled']), (False, 'too_soon', 1))
        self.assertEqual(c['why'], 'too_soon')
        self.assertEqual(self.runs, ['google'] * 3)

    def test_elevate_kick_and_sync_only_for_enabled_two_way_names(self):
        host = SimpleNamespace(st=SimpleNamespace(root=self.root, log=lambda *a, **kw: None), _calrun=self.r)
        self.write(subs={'google': dict(GOOGLE), 'off': dict(GOOGLE, enabled=False), 'personal': {'enabled': True, 'key': 'CALENDAR_ICS_SECRET'}})
        async def go():
            out = []
            for op in ('kick', 'sync'):
                for n in ('nosuch', 'off', 'personal'):
                    out.append(await rm.elevate_request(host, host.st, {'t': 'calsync', 'op': op, 'name': n}))
            return out
        out = asyncio.run(go())
        self.assertEqual([x['why'] for x in out], ['not_found', 'disabled', 'not_found'] * 2)
        self.assertEqual((self.r.timers, self.r.locks, self.runs), ({}, {}, []))

    def test_locks_and_results_of_removed_remotes_pruned(self):
        async def go():
            await self.r.now('google')
            self.assertIn('google', self.r.locks)
            self.write(subs={})
            await self.r.now('google')
        asyncio.run(go())
        self.assertEqual((self.r.locks, self.r.last, self.r.fails), ({}, {}, {}))

    def test_debounce_capped_at_30_s_after_the_first_kick(self):
        delays = []
        async def go():
            async def sleep(d):
                delays.append(d)
                await asyncio.Event().wait()                     # never fires: we only look at the delays asked for
            self.r.sleep = sleep
            for _ in range(10):
                self.r.kick('google')
                await asyncio.sleep(0)
                self.t[0] += 4
            for t in self.r.timers.values():
                t.cancel()
        asyncio.run(go())
        self.assertEqual(delays[:7], [5.0] * 7)
        self.assertEqual(delays[7:], [2.0, 0.0, 0.0])           # first kick at t, now t+28 / t+32 / t+36: due at t+30


class Phone(Base):
    def host(self):
        logs = []
        return SimpleNamespace(st=SimpleNamespace(root=self.root, log=lambda *a, **kw: logs.append((a, kw)))), logs

    def test_frame_ops_and_refusals(self):
        from agentj.serve import Host
        self.write([self.linked()])
        host, logs = self.host()
        host.lang, replies = 'en', []
        host.send_app = mock.AsyncMock(side_effect=lambda s, m: replies.append(m))
        rev = cal.listing(self.root)['revision']
        sess = SimpleNamespace(device='paired')
        fake = Fake()
        fake.rows['gid1'] = {**cal.event(T), 'rid': 'gid1', 'etag': '"1"'}
        async def go():
            for i, bad in enumerate(({'op': 'undo'}, {'op': 'resolve', 'entry': 'e-1', 'keep': 'local'}, {'op': 'nuke', 'revision': rev},
                                     {'op': 'sync'}, {'op': 'sync', 'name': 'personal'}, {'op': 'undo', 'revision': rev, 'id': 'notes'})):
                await Host.on_jcard(host, sess, 'jcard_cal', {'r': 'bad0000%d' % i, 'id': 'calendar', **bad})
            await Host.on_jcard(host, sess, 'jcard_cal', {'r': 'abcd1234', 'id': 'calendar', 'op': 'edit', 'entry': 'e-1',
                                                          'revision': rev, 'event': {'title': 'Phone edit'}})
            with mock.patch.object(rm, 'adapter_for', return_value=fake), mock.patch.object(rm.Runner, 'kick'):
                await Host.on_jcard(host, sess, 'jcard_cal', {'r': 'abcd1235', 'id': 'calendar', 'op': 'sync', 'name': 'google'})
            await Host.on_jcard(host, sess, 'jcard_cal', {'r': 'abcd1236', 'id': 'calendar', 'op': 'undo',
                                                          'revision': replies[-1]['revision']})
        with mock.patch.object(rm.Runner, 'kick') as kick:
            asyncio.run(go())
        self.assertTrue(all(r['ok'] is False and r['t'] == 'jcard_cal' for r in replies[:6]), replies[:6])
        self.assertEqual([r['why'] for r in replies[:6]], ['bad_op', 'bad_op', 'bad_op', 'not_two_way', 'not_two_way', 'bad_op'])
        edit, synced, undone = replies[6:]
        self.assertTrue(edit['ok'] and edit['events'][0]['pending'])
        kick.assert_called_with('google')                         # a queued change wakes the runner
        self.assertEqual((synced['sync']['ok'], synced['sync']['pushed'], synced['events'][0]['pending']), (True, 1, False))
        self.assertEqual(fake.rows['gid1']['title'], 'Phone edit')
        self.assertEqual((undone['ok'], undone['events'][0]['title'], undone['events'][0]['pending']), (True, 'Meeting', True))
        self.assertNotIn('Phone edit', str(logs))
        self.assertNotIn('gid1', json.dumps(replies))


class RunnerTests(unittest.TestCase):
    def test_debounce_backoff_and_auto(self):
        tmp = tempfile.TemporaryDirectory(prefix='p428-')
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        jcards._write_json(root, ('cards-data', 'calendar'), 'calendar.json',
                           {'schema': 1, 'events': [], 'subscriptions': {'google': dict(GOOGLE, auto=True)}})
        delays, results, runs = [], [{'ok': False, 'why': 'offline'}, {'ok': False, 'why': 'offline'}, {'ok': True}], []
        async def go():
            gate = {}
            async def sleep(d):
                delays.append(d)
                f = gate[len(delays)] = asyncio.get_running_loop().create_future()
                await f
            r = rm.Runner(SimpleNamespace(root=root), run=lambda n: (runs.append(n), results.pop(0))[1], sleep=sleep)
            for _ in range(3):                                              # three changes → one sync
                r.kick('google')
                await asyncio.sleep(0)
            async def release(i):
                gate[i].set_result(None)
                for _ in range(20):
                    await asyncio.sleep(0.01)
            await release(3)
            self.assertEqual(runs, ['google'])
            await release(4)                                                 # first failure: 60 s; second: 120 s
            await release(5)
            for t in r.timers.values():
                t.cancel()
        asyncio.run(go())
        self.assertEqual(delays, [5.0, 5.0, 5.0, 60.0, 120.0, 900.0])
        self.assertEqual(runs, ['google'] * 3)
        self.assertEqual(rm.Runner.CAP, 3600.0)
        self.assertEqual(min(rm.Runner.BASE * 2 ** 9, rm.Runner.CAP), 3600.0)

    def test_nothing_scheduled_without_two_way_remote(self):
        tmp = tempfile.TemporaryDirectory(prefix='p428-')
        self.addCleanup(tmp.cleanup)
        host = SimpleNamespace(st=SimpleNamespace(root=pathlib.Path(tmp.name)))
        cal.change(host.st.root, 'add', **T)
        asyncio.run(rm.start(host))
        rm.after_change(host, cal.listing(host.st.root))
        self.assertFalse(hasattr(host, '_calrun'))


if __name__ == '__main__':
    unittest.main()
