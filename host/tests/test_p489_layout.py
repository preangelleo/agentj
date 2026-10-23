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
        s=SimpleNamespace(state='ready',p33=True,cid='cid',pub=b'public')
        host=SimpleNamespace(st=self.st,sessions={'cid':s},send_app=AsyncMock(),close_cid=AsyncMock())
        obj={'t':'layout_diag','r':'abcdefghijklmnop','sample':SAMPLE}
        with patch.object(self.st,'is_allowed',return_value=True):
            await Host._app(host,s,obj)
            self.assertTrue(host.send_app.call_args.args[1]['ok'])
            before=layout_diag.path(self.st).read_bytes()
            await Host._app(host,s,obj)
            self.assertEqual(host.send_app.call_args.args[1]['why'],'rate')
            self.assertEqual(before,layout_diag.path(self.st).read_bytes())
        with patch.object(self.st,'is_allowed',return_value=False):
            await Host._app(host,s,obj);host.close_cid.assert_awaited_once_with('cid','revoked')
        s.state='pending';host.send_app.reset_mock()
        await Host._app(host,s,obj);host.send_app.assert_not_awaited()
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
