import _hermetic  # noqa
import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agentj.state import State
from agentj import layout_diag

SAMPLE = {'v':1,'at':1792754640000,'ios':True,'standalone':False,'ua':'chrome','theme':'dark',
          'status':'idle','conn':'up','kb':False,'known':True,'compacting':False,'level':0.12,
          'bottom':'rgb(69, 102, 92)','innerHeight':844,'clientHeight':844,'vv':{'height':844,'offsetTop':0,'scale':1},
          'layers':{'water':{'rect':{'x':0,'y':700,'width':390,'height':144},'background':'rgb(69, 102, 92)','display':'block','opacity':1,'transform':'none'}},
          'meter':{'ctx':12,'h5':4,'week':68},'look':{'factory':'robots','custom':False},
          'build':{'version':'0.25.3a1','combined':'a'*64,'iosCode':'b'*64}}
class Layout(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=State(Path(self.tmp.name));self.st.init()
    async def asyncTearDown(self): self.tmp.cleanup()
    async def test_receipt_file_log_and_read_only_last(self):
        events=[];self.st.log=lambda *a,**kw:events.append((a,kw))
        self.assertEqual(layout_diag.save(self.st,SAMPLE),True)
        path=layout_diag.path(self.st);self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(json.loads(path.read_text())['sample'],SAMPLE)
        before=path.read_bytes();out=io.StringIO()
        self.assertEqual(layout_diag.last(self.st,out),0);self.assertEqual(before,path.read_bytes())
        self.assertEqual(events,[(('layout_diag',),{'status':'saved','bytes':len(before)})])
        self.assertNotIn('a'*64,str(events))
    async def test_reject_extra_keys_and_strings_before_disk(self):
        for sample in [dict(SAMPLE,url='secret'),dict(SAMPLE,theme='secret'),dict(SAMPLE,layers={'water':{'url':'secret'}}),dict(SAMPLE,innerHeight=float('nan')),dict(SAMPLE,meter={'ctx':'secret'}),dict(SAMPLE,look={'factory':'secret','custom':True})]:
            with self.subTest(sample=sample):self.assertFalse(layout_diag.save(self.st,sample));self.assertFalse(layout_diag.path(self.st).exists())
    async def test_missing_and_corrupt_never_echo_file(self):
        for content in [None,'{"secret":"do not print"}']:
            if content: layout_diag.path(self.st).write_text(content)
            out=io.StringIO();self.assertEqual(layout_diag.last(self.st,out),1);self.assertNotIn('do not print',out.getvalue())
    async def test_no_follow_read(self):
        target=self.st.root/'private';target.write_text('secret');layout_diag.path(self.st).symlink_to(target)
        out=io.StringIO();self.assertEqual(layout_diag.last(self.st,out),1);self.assertNotIn('secret',out.getvalue())
    async def test_ready_gate_rate_and_revocation(self):
        from agentj.serve import Host
        from unittest.mock import AsyncMock
        # Fixture follows 4676679f3a (0.30.0 paired ingress): every app message is framed against the session's device
        # binding and the live host identity before its handler runs, so the stand-ins carry those fields.
        s=SimpleNamespace(state='ready',p33=True,cid='cid',pub=b'public',device='synthetic-remote')
        host=SimpleNamespace(st=self.st,sessions={'cid':s},send_app=AsyncMock(),close_cid=AsyncMock(),
                             kp=SimpleNamespace(pub=b'h'*32),channel='synthetic-channel',hist=SimpleNamespace(epoch='synthetic'),
                             stopped=lambda:False,stopping=asyncio.Event())
        host._app_frame=lambda session,message:Host._app_frame(host,session,message)   # the real handler, inside the real frame
        obj={'t':'layout_diag','r':'abcdefghijklmnop','sample':SAMPLE}
        with patch.object(self.st,'is_allowed',return_value=True):
            await Host._app_frame(host,s,obj)
            self.assertTrue(host.send_app.call_args.args[1]['ok'])
            before=layout_diag.path(self.st).read_bytes()
            await Host._app_frame(host,s,obj)
            self.assertEqual(host.send_app.call_args.args[1]['why'],'rate')
            self.assertEqual(before,layout_diag.path(self.st).read_bytes())
        with patch.object(self.st,'is_allowed',return_value=False):
            await Host._app_frame(host,s,obj);host.close_cid.assert_awaited_once_with('cid','revoked')
        s.state='pending';host.send_app.reset_mock()
        await Host._app_frame(host,s,obj);host.send_app.assert_not_awaited()
    async def test_legacy_upload_is_temporary_and_bytes_preserved(self):
        from agentj.uploads import Uploads
        import hashlib
        import base64
        work=self.st.root/'work';work.mkdir();clock=[1000]
        uploads=Uploads(self.st,str(work),clock=lambda:clock[0]);raw=json.dumps(SAMPLE).encode()
        dev='A'*16;bid='A'*22
        opened=uploads.open(dev,{'bid':bid,'purpose':'att','origin':'file','mime':'application/json','name':'layout-diagnostic.json','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        self.assertEqual(opened[0]['t'],'blob_ack')
        uploads.chunk(dev,{'bid':bid,'o':0,'d':base64.urlsafe_b64encode(raw).decode().rstrip('=')})
        out,_=uploads.end(dev,bid);self.assertTrue(out[-1]['ok'])
        b=uploads.blobs[(dev,bid)];p=Path(b.path);self.assertEqual(p.read_bytes(),raw)
        clock[0]+=1801;uploads.sweep();self.assertFalse(p.exists())
    async def test_fenced_reader_uses_fixed_existing_socket_operation(self):
        from agentj import host_diagnostics as hd
        out=io.StringIO()
        layout_diag.save(self.st,SAMPLE)
        result=hd.execute(self.st,{'t':'host_diagnostics','op':'layout_last'})
        self.assertEqual(result['exit'],0);self.assertEqual(json.loads(result['output'])['sample'],SAMPLE)
        self.assertEqual(hd.execute(self.st,{'t':'host_diagnostics','op':'layout_last','path':'secret'})['exit'],1)

    async def test_cli_is_read_only_before_handler_and_preserves_exit(self):
        from agentj import cli, migrate, host_diagnostics as hd
        with patch.object(hd,'request',return_value=7) as request, patch.object(migrate,'auto',side_effect=AssertionError('diagnostic must not migrate')):
            with self.assertRaises(SystemExit) as result: cli.main(['diag','layout','--last'])
            self.assertEqual(result.exception.code,7);request.assert_called_once_with('layout_last')

RENDER = {'reason':'context_lost','agent':'android-chrome','alive':'lt6h','hidden':12,'hiddenMax':'lt1h','gl':'lost','glLost':1,'glRestored':0,
          'mem':8,'cache':'lt32mb','cards':'lt500','nodes':'lt5000'}
class RenderCounters(unittest.TestCase):
    """Render-health counters ride the same sample as one optional, closed object (state under a temp dir, never HOME)."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=State(Path(self.tmp.name));self.st.init()
    def tearDown(self): self.tmp.cleanup()
    def test_sample_with_counters_is_saved_and_read_back(self):
        sample=dict(SAMPLE,render=RENDER)
        self.assertTrue(layout_diag.save(self.st,sample))
        self.assertEqual(json.loads(layout_diag.path(self.st).read_text())['sample'],sample)
        out=io.StringIO();self.assertEqual(layout_diag.last(self.st,out),0);self.assertEqual(json.loads(out.getvalue())['sample']['render'],RENDER)
        self.assertTrue(layout_diag.save(self.st,dict(SAMPLE,render=dict(RENDER,mem=None,reason='manual',gl='unavailable'))))
    def test_sample_without_counters_still_valid(self):
        self.assertTrue(layout_diag.valid(SAMPLE))
    def test_every_enum_value_the_page_can_send_is_accepted(self):
        for key,values in layout_diag.RENDER_ENUMS.items():
            for value in values:
                with self.subTest(key=key,value=value):self.assertTrue(layout_diag.valid(dict(SAMPLE,render=dict(RENDER,**{key:value}))))
    def test_free_text_extra_keys_and_out_of_range_never_reach_disk(self):
        bad=[dict(RENDER,note='secret'),dict(RENDER,reason='secret text'),dict(RENDER,agent='Mozilla/5.0 (Linux; Android 15)'),dict(RENDER,alive=3600),
             dict(RENDER,hidden=-1),dict(RENDER,hidden=10000),dict(RENDER,hidden=1.5),dict(RENDER,hidden=True),dict(RENDER,glLost='1'),dict(RENDER,glRestored=100),
             dict(RENDER,mem=3),dict(RENDER,mem='8'),dict(RENDER,mem=True),dict(RENDER,mem=float('nan')),dict(RENDER,cache='https://example.com/x'),
             dict(RENDER,cards=497),dict(RENDER,nodes=['lt500']),dict(RENDER,gl={}),{k:v for k,v in RENDER.items() if k!='gl'},'secret',None,[],{}]
        for render in bad:
            with self.subTest(render=render):
                self.assertFalse(layout_diag.save(self.st,dict(SAMPLE,render=render)));self.assertFalse(layout_diag.path(self.st).exists())
        self.assertFalse(layout_diag.save(self.st,dict(SAMPLE,render=RENDER,url='secret')))

class AutoSampleRate(unittest.IsolatedAsyncioTestCase):
    """An unattended sample rides the same five-second-per-session limit as the owner's own (frames enter at _app_frame,
    where the ready / allowlist / p33 gates and the layout_diag branch are; _app is only the ingress wrapper)."""
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=State(Path(self.tmp.name));self.st.init()
    async def asyncTearDown(self): self.tmp.cleanup()
    async def test_auto_sample_inside_five_seconds_is_refused_and_changes_nothing(self):
        from agentj.serve import Host
        from unittest.mock import AsyncMock
        s=SimpleNamespace(state='ready',p33=True,cid='cid',pub=b'public')
        host=SimpleNamespace(st=self.st,sessions={'cid':s},send_app=AsyncMock(),close_cid=AsyncMock())
        manual={'t':'layout_diag','r':'abcdefghijklmnop','sample':dict(SAMPLE,render=dict(RENDER,reason='manual',gl='ok',glLost=0))}
        auto={'t':'layout_diag','r':'ponmlkjihgfedcba','sample':dict(SAMPLE,render=RENDER)}
        with patch.object(self.st,'is_allowed',return_value=True):   # elapsed time = moving the session's own last-sample mark back
            await Host._app_frame(host,s,manual)
            self.assertEqual(host.send_app.call_args.args[1],{'t':'layout_diag_res','r':manual['r'],'ok':True})
            before=layout_diag.path(self.st).read_bytes()
            s.layout_diag_at-=4.5;await Host._app_frame(host,s,auto)
            self.assertEqual(host.send_app.call_args.args[1],{'t':'layout_diag_res','r':auto['r'],'ok':False,'why':'rate'})
            self.assertEqual(before,layout_diag.path(self.st).read_bytes())
            s.layout_diag_at-=0.6;await Host._app_frame(host,s,auto)        # 5.1 s after the saved one: a refused sample did not extend the wait
            self.assertTrue(host.send_app.call_args.args[1]['ok'])
            self.assertEqual(json.loads(layout_diag.path(self.st).read_text())['sample']['render']['reason'],'context_lost')
            s.p33=False;s.layout_diag_at-=60;host.send_app.reset_mock();await Host._app_frame(host,s,auto);host.send_app.assert_not_awaited()

class LayoutLastCaller(unittest.TestCase):
    """`agentj diag layout --last` through the elevate socket answers only the owner's own Agent session."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.st=State(Path(self.tmp.name));self.st.init();layout_diag.save(self.st,dict(SAMPLE,render=RENDER))
    def tearDown(self): self.tmp.cleanup()
    def refusal(self,pid,chain,owner,op='layout_last'):
        from agentj import host_diagnostics as hd, shared_hook
        host=SimpleNamespace(_agent_pids=owner if callable(owner) else (lambda:owner))
        with patch.object(shared_hook,'peer_pid',return_value=(pid,1000)), patch.object(shared_hook,'ancestors',return_value=chain):
            return hd.caller_refusal({'t':'host_diagnostics','op':op},object(),host)
    def test_owner_agent_session_reads(self):
        from agentj import host_diagnostics as hd
        self.assertIn('layout_last',hd.READ_ONLY)
        self.assertIsNone(self.refusal(4242,[4242,4000,3000],{3000}))
        result=hd.execute(self.st,{'t':'host_diagnostics','op':'layout_last'})
        self.assertEqual(result['exit'],0);self.assertEqual(json.loads(result['output'])['sample']['render'],RENDER)
    def test_friend_visitor_and_unknown_callers_are_refused_without_the_sample(self):
        from agentj import host_diagnostics as hd
        self.assertEqual(self.refusal(5151,[5151,5000,1],{3000}),'not_owner_agent')      # another child of serve (friend / visitor / task)
        self.assertEqual(self.refusal(5151,[5151,5000,1],set()),'not_owner_agent')       # no owner session known: nobody qualifies
        def broken(): raise RuntimeError('no agent')
        self.assertEqual(self.refusal(5151,[5151,3000],broken),'not_owner_agent')
        for pid in (None,0,1,'4242'):
            with self.subTest(pid=pid):self.assertEqual(self.refusal(pid,[3000],{3000}),'peer_unverified')
        self.assertEqual(hd.caller_refusal({'t':'host_diagnostics','op':'layout_last'},None,SimpleNamespace(_agent_pids=lambda:{3000})),'peer_unverified')
        out=hd.refused('not_owner_agent')
        self.assertEqual(out['exit'],1);self.assertNotIn('context_lost',out['output']);self.assertNotIn('android-chrome',out['output']);self.assertNotIn('rgb(',out['output'])
    def test_served_request_from_a_non_owner_never_reaches_the_reader(self):
        from agentj import host_diagnostics as hd
        with patch.object(layout_diag,'last',side_effect=AssertionError('reader reached')):
            why=self.refusal(5151,[5151,5000],{3000})
            res=hd.refused(why) if why else hd.execute(self.st,{'t':'host_diagnostics','op':'layout_last'})
        self.assertEqual(res['exit'],1)
