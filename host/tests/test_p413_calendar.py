"""P413 calendar storage/CLI/due/ICS/security and paired remote boundaries."""
import _hermetic  # noqa: F401: isolated test HOME/state before importing the host
import asyncio
import contextlib
import datetime as dt
import io
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import jcards, jcards_cal as cal, jcards_cal_sync as sync, jcards_todo as todo, state

NOW = dt.datetime(2026, 10, 20, 9, tzinfo=dt.timezone.utc)
ICS = b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:one\r\nDTSTART:20261020T093000Z\r\nDTEND:20261020T103000Z\r\nSUMMARY:Meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'

class Calendar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p413-')
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.event = {'title': 'Meeting', 'start': '2026-10-20T09:30:00+00:00', 'end': '2026-10-20T10:30:00+00:00', 'reminder': 45}
    def add(self, **kw):
        result = cal.change(self.root, 'add', **(self.event | kw))
        self.assertTrue(result['ok'], result)
        return result
    def test_default_on_crud_revision(self):
        c = jcards.find(self.root, 'calendar')
        self.assertTrue(c['on'])
        self.assertEqual(jcards.check(c['doc'], official=True), [])
        first = self.add()
        e = first['events'][0]
        r = cal.change(self.root, 'edit', e['id'], first['revision'], title='Changed')
        self.assertTrue(r['ok'])
        self.assertEqual(cal.change(self.root, 'delete', e['id'], first['revision'])['why'], 'conflict')
        self.assertTrue(cal.change(self.root, 'delete', e['id'], r['revision'])['ok'])
        self.assertEqual(cal.listing(self.root)['events'], [])
    def test_off_persists(self):
        jcards.set_on(self.root, 'calendar', False)
        self.add()
        self.assertEqual(jcards.phone_get(self.root, 'calendar')['why'], 'off')
        self.assertEqual(len(cal.listing(self.root)['events']), 1)
        jcards.set_on(self.root, 'calendar', True)
        self.assertEqual(jcards.phone_get(self.root, 'calendar')['template'], 'calendar')
    def test_time_all_day_and_validation(self):
        for bad in ({'start': '2026-10-20T09:00'}, {'end': '2026-10-19T10:00Z'}, {'all_day': 'true'}, {'reminder': -1}, {'reminder': True}, {'title': ''}, {'note': 42}, {'start': None}, {'start': '20261020T093000Z'}):
            self.assertFalse(cal.change(self.root, 'add', **(self.event | bad))['ok'], bad)
        r = cal.change(self.root, 'add', title='Holiday', all_day=True, start='2026-10-20')
        self.assertTrue(r['ok'])
        self.assertEqual(r['events'][0]['end'], '2026-10-21')
        self.assertFalse(cal.change(self.root, 'add', title='x', all_day=True, start='2026-10-20', end='2026-10-20')['ok'])
    def test_due_reminder_window_and_today(self):
        self.add(start='2026-10-21T00:10Z', end='2026-10-21T01:00Z', reminder=30)
        before = dt.datetime(2026,10,20,23,30,tzinfo=dt.timezone.utc)
        with mock.patch.dict('os.environ', {'TZ': 'UTC'}):
            import time
            time.tzset()
            try:
                self.assertEqual(cal.listing(self.root, 'due', before)['events'], [])
                self.assertEqual(len(cal.listing(self.root, 'due', before+dt.timedelta(minutes=15))['events']), 1)
                self.assertEqual(cal.listing(self.root, 'due', before+dt.timedelta(hours=2))['events'], [])
            finally:
                pass
        import time;time.tzset()
    def test_all_day_reminder_and_exclusive_end(self):
        r=cal.change(self.root,'add',title='Holiday',all_day=True,start='2026-10-21',reminder=30)
        self.assertTrue(r['ok'])
        midnight=dt.datetime.combine(dt.date(2026,10,21),dt.time()).astimezone()
        self.assertEqual(len(cal.listing(self.root,'due',midnight-dt.timedelta(minutes=15))['events']),1)
        self.assertEqual(cal.listing(self.root,'due',midnight-dt.timedelta(minutes=31))['events'],[])
        self.assertEqual(cal.listing(self.root,'due',midnight+dt.timedelta(days=1))['events'],[])
    def test_todo_projection_does_not_copy(self):
        todo.change(self.root, 'add', text='Timed task', owner='agent', start='2026-10-20T09:00Z')
        rows = cal.listing(self.root)['events']
        self.assertTrue(any(e['readonly'] and e['source'].startswith('card/todo#') for e in rows))
        self.assertEqual(cal.load(self.root)[0]['events'], [])
    def test_cli(self):
        def run(*args):
            out = io.StringIO()
            with mock.patch.object(state, 'state_dir', return_value=self.root), contextlib.redirect_stdout(out):
                code = jcards.command(['cal', *args, '--json'])
            return code,json.loads(out.getvalue())
        code,res=run('add','--title','Holiday','--start','2026-10-20','--all-day')
        self.assertEqual(code,0)
        ident=res['events'][0]['id']
        self.assertEqual(run('edit','--entry',ident,'--title','Break')[0],0)
        self.assertEqual(run('delete','--entry',ident)[0],0)
        self.assertEqual(run('list')[1]['events'],[])
        self.assertEqual(run('due')[0],0)
        self.assertEqual(run('add','--title','No offset','--start','2026-10-20T09:00')[0],1)
    def test_symlink_and_corruption_refused(self):
        self.add()
        p=jcards.data_dir(self.root,'calendar')/'calendar.json'
        target=self.root/'elsewhere';target.write_text('KEEP')
        p.unlink();p.symlink_to(target)
        self.assertFalse(cal.change(self.root,'add',**self.event)['ok'])
        self.assertEqual(target.read_text(),'KEEP')
        p.unlink();p.write_text('{broken')
        self.assertFalse(cal.change(self.root,'add',**self.event)['ok'])
        self.assertEqual(p.read_text(),'{broken')
    def test_secrets_never_emitted(self):
        secret='sk-'+'TESTcalendarSecret1234567890'*2
        self.assertFalse(cal.change(self.root,'add',**(self.event|{'note':secret}))['ok'])
        self.add()
        doc,rev=cal.load(self.root);doc['events'][0]['note']=secret
        jcards._write_json(self.root,('cards-data','calendar'),'calendar.json',doc)
        self.assertNotIn(secret,json.dumps(cal.listing(self.root)))
        doc['events'][0]['note']=''
        doc['events'][0]['id']='ghp_'+'Ab1cDef2Gh3Ij4Kl5Mn6Op7Qr8St9Uv0Wx1Yz2'
        jcards._write_json(self.root,('cards-data','calendar'),'calendar.json',doc)
        self.assertNotIn(doc['events'][0]['id'],json.dumps(cal.listing(self.root)))
    def test_bounded_storage(self):
        doc={'schema':1,'events':[dict(cal.event(self.event),id='e'+str(i)) for i in range(cal.MAX_EVENTS)],'subscriptions':{}}
        jcards._write_json(self.root,('cards-data','calendar'),'calendar.json',doc)
        self.assertEqual(cal.change(self.root,'add',**self.event)['why'],'full')
    def test_subscribe_sync_remote_wins_and_local_survives(self):
        self.add(title='Local')
        self.assertEqual(sync.command(self.root,'sync','personal')['why'],'disabled')
        self.assertTrue(sync.command(self.root,'subscribe','personal','CALENDAR_ICS_SECRET')['ok'])
        key=mock.Mock(return_value='https://calendar.example/feed')
        r=sync.command(self.root,'sync','personal',get=lambda u:ICS,read_key=key)
        key.assert_called_once_with('CALENDAR_ICS_SECRET')
        self.assertTrue(r['ok']);self.assertTrue(r['remote_wins']);self.assertEqual(len(r['events']),2)
        remote=next(x for x in r['events'] if x['readonly'])
        self.assertEqual(cal.change(self.root,'edit',remote['id'],title='Local edit')['why'],'readonly')
        # Agent hand-edited a cached remote row; remote still wins and reports the replacement.
        doc,_=cal.load(self.root);next(x for x in doc['events'] if x.get('subscription'))['title']='Conflict'
        jcards._write_json(self.root,('cards-data','calendar'),'calendar.json',doc)
        r=sync.command(self.root,'sync','personal',get=lambda u:ICS,read_key=key)
        self.assertTrue(r['remote_wins']);self.assertNotIn('Conflict',[x['title'] for x in r['events']])
        before=cal.load(self.root)
        r=sync.command(self.root,'sync','personal',get=mock.Mock(side_effect=ValueError('https://private-secret')),read_key=key)
        self.assertEqual(r,{'ok':False,'why':'sync_failed'});self.assertEqual(cal.load(self.root),before)
        self.assertTrue(sync.command(self.root,'unsubscribe','personal')['ok'])
        self.assertEqual([x['title'] for x in cal.listing(self.root)['events']],['Local'])
    def test_sync_cas_on_concurrent_owner_edit(self):
        sync.command(self.root,'subscribe','personal','CALENDAR_ICS_SECRET')
        def fetch(_):
            self.add(title='Concurrent')
            return ICS
        r=sync.command(self.root,'sync','personal',get=fetch,read_key=lambda n:'https://example.com')
        self.assertEqual(r['why'],'conflict')
        self.assertEqual(len(cal.listing(self.root)['events']),1)
    def test_ics_folded_all_day_timezone_recurrence_and_duplicates(self):
        self.assertEqual(sync.parse(ICS.replace(b'SUMMARY:Meeting',b'SUMMARY:Meet\r\n ing'),'personal')[0]['title'],'Meeting')
        z=ICS.replace(b'DTSTART:20261020T093000Z',b'DTSTART;TZID=Asia/Tokyo:20261020T093000').replace(b'DTEND:20261020T103000Z',b'DTEND;TZID=Asia/Tokyo:20261020T103000')
        self.assertIn('+09:00',sync.parse(z,'personal')[0]['start'])
        for raw in (ICS.replace(b'SUMMARY:',b'RRULE:FREQ=DAILY\r\nSUMMARY:'), ICS.replace(b'DTEND:20261020T103000Z',b'DURATION:PT2H'), b'broken', ICS.replace(b'END:VCALENDAR',ICS.split(b'BEGIN:VCALENDAR\r\n')[1])):
            with self.assertRaises((ValueError,KeyError)):
                sync.parse(raw,'personal')
    def test_fetch_refuses_private_redirect_credentials_and_http(self):
        for u in ('http://example.com/a','https://u:p@example.com/a','https://example.com:8443/a'):
            with self.assertRaises(ValueError):sync.fetch(u)
        with mock.patch.object(socket := sync.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):sync.fetch('https://example.com/a')
    def test_phone_protocol_and_log_metadata(self):
        from agentj.serve import Host
        replies=[];logs=[]
        host=SimpleNamespace(st=SimpleNamespace(root=self.root,log=lambda *a,**kw:logs.append((a,kw))),lang='en',send_app=mock.AsyncMock(side_effect=lambda s,m:replies.append(m)))
        revision=cal.listing(self.root)['revision']
        async def go():
            await Host.on_jcard(host,SimpleNamespace(device='paired'), 'jcard_cal', {'r':'abcd1234','id':'calendar','op':'add','revision':revision,'event':self.event})
            await Host.on_jcard(host,SimpleNamespace(device='paired'), 'jcard_cal', {'r':'abcd1235','id':'calendar','op':'sync','revision':revision})
        asyncio.run(go())
        self.assertTrue(replies[0]['ok']);self.assertFalse(replies[1]['ok'])
        self.assertNotIn('Meeting',str(logs));self.assertEqual(logs[0][1]['device'],'paired')

if __name__=='__main__':unittest.main()
