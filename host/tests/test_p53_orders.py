"""#53: offline authority, real Unix peer credentials and owned process lifecycle."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from agentj import provenance
from agentj.core import dispatcher, orders, order_host, askbridge

BRIEF = {'goal':'offline','scope':{'write':['result.txt']},'verify':['offline'],'timebox':1,'report':'head'}
HEAD = '完成：1/1\nready：是\n需要上级：无\nblocked-by: -\n\nVERDICT: ok — synthetic'

class Authority(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.project = self.root/'project'; self.project.mkdir()
        self.sid = 'session53'
        self.ag = NS(kind='core',cfg={'dir':str(self.project),'fence':True},native_source=provenance.phone())
        self.log = []
        self.host = NS(agent=self.ag, stopped=lambda:False,
                       st=NS(root=self.root/'state',agent_session=lambda kind:self.sid,log=lambda *a,**kw:self.log.append((a,kw))))
        env = mock.patch.dict(os.environ,{'AGENTJ_CORE':'1','AGENTJ_ORDER_LAUNCHER':'proc'})
        env.start(); self.addCleanup(env.stop)
    async def call(self,args):
        return await order_host.handle(self.host,{'args':args,'nonce':secrets.token_hex(16)},internal=self.ag)
    def order(self):
        return orders.create(project=str(self.project),prompt_text='synthetic',title='test',brief=BRIEF,
                             parent={'kind':'owner_turn','session':self.sid,'harness':'core'},st_root=self.host.st.root)
    async def test_full_six_actions_with_owned_subprocess(self):
        script = self.root/'synthetic.py'
        script.write_text('''import os,sys,time,pathlib
from agentj.core import orders
p=pathlib.Path(os.environ['AGENTJ_STATE_DIR'])/'orders'/sys.argv[1]
o=orders.Order(p)
(p/'pid').write_text(str(os.getpid()))
o.event('started')
if not list((p/'inbox').glob('*.md')):
 (p/'synthetic-ready').write_text('1')
 while o.control() != 'stop': time.sleep(.01)
 state='stopped'
else: state='done'
orders._write(p/'report.md',sys.argv[2])
o.write_result({'state':state,'exit_code':0,'head':orders.parse_head(sys.argv[2]),'verdict':'done'})
''')
        with mock.patch.object(dispatcher,'ceo_argv',lambda o:[sys.executable,str(script),o.id,HEAD]), mock.patch.object(dispatcher,'STOP_GRACE',.5):
            r=await self.call({'action':'new','project':str(self.project),'prompt':'synthetic','brief':BRIEF})
            self.assertEqual(r['result'],'ok',r); oid=r['id']; o=orders.get(oid,self.host.st.root)
            try:
                end=time.monotonic()+5
                while not (o.path/'synthetic-ready').exists() and time.monotonic()<end: await asyncio.sleep(.01)
                self.assertTrue((o.path/'synthetic-ready').exists())
                self.assertEqual((await self.call({'action':'status','id':oid}))['result'],'ok')
                self.assertEqual((await self.call({'action':'send','id':oid,'text':'all additions'}))['result'],'ok')
                self.assertEqual((await self.call({'action':'stop','id':oid}))['result'],'ok')
                self.assertEqual(o.result()['state'],'stopped')
                self.assertEqual((await self.call({'action':'resume','id':oid}))['result'],'ok')
                end=time.monotonic()+10
                while not o.result() and time.monotonic()<end: await asyncio.sleep(.02)
                self.assertEqual(o.result()['state'],'done')
                self.assertIn('synthetic',(await self.call({'action':'report','id':oid}))['text'])
                self.assertEqual(o.inbox_all(),[(1,'all additions')])
                self.assertEqual(o.events()[0]['ev'],'origin')
            finally: await asyncio.to_thread(dispatcher.stop,o,grace=0)
    async def test_external_unknown_sources_fail_closed(self):
        for source in (None,provenance.external_mcp('a'*22),provenance.email_guest('x@example.test'),{'kind':'owner_turn','role':'owner'}):
            self.ag.native_source=source
            self.assertEqual((await self.call({'action':'status','id':'no'}))['why'],'source')
    async def test_model_identity_and_policy_rejected(self):
        for key,value in [('origin','owner_turn'),('parent',{}),('budget',{'tokens':-1}),('fence',False),('st_root','/outside'),('grants',{})]:
            self.assertEqual((await self.call({'action':'new',key:value}))['why'],'shape')
    async def test_wrong_session_project_cross_order(self):
        o=self.order(); self.sid='other'
        self.assertEqual((await self.call({'action':'report','id':o.id}))['why'],'order_owner')
        self.sid='session53'; self.ag.cfg['dir']=str(self.root)
        self.assertEqual((await self.call({'action':'stop','id':o.id}))['why'],'order_owner')
    async def test_symlink_project_escape(self):
        p=self.project/'escape'; p.symlink_to(self.root,target_is_directory=True)
        self.assertEqual((await self.call({'action':'new','project':str(p)}))['why'],'project_not_allowed')
    async def test_stop_and_off(self):
        self.host.stopped=lambda:True
        self.assertEqual((await self.call({'action':'status'}))['why'],'stopped_or_off')
        self.host.stopped=lambda:False
        with mock.patch.dict(os.environ,{'AGENTJ_CORE':'0'}):
            self.assertEqual((await self.call({'action':'status'}))['why'],'stopped_or_off')
    async def test_budget_cannot_resume(self):
        o=self.order(); o.write_result({'state':'budget'})
        self.assertEqual((await self.call({'action':'resume','id':o.id}))['why'],'budget_requires_owner')
    async def test_replay_cannot_append_twice(self):
        o=self.order(); req={'args':{'action':'send','id':o.id,'text':'once'},'nonce':secrets.token_hex(16)}
        self.assertEqual((await order_host.handle(self.host,req,internal=self.ag))['result'],'ok')
        self.assertEqual((await order_host.handle(self.host,req,internal=self.ag))['why'],'replay')
        self.assertEqual(len(o.inbox_all()),1)
    async def test_native_not_silently_launched(self):
        for kind in ('claude','codex'):
            r=await self.call({'action':'new','project':str(self.project),'brief':BRIEF,'harness':kind})
            self.assertEqual(r['why'],'empty')
        self.assertEqual(orders.all_orders(self.host.st.root),[])
    async def test_unix_socket_peer_binding(self):
        o=self.order(); sock=self.root/'host.sock'
        async def serve(reader,writer):
            req=json.loads(await reader.readline())
            res=await order_host.handle(self.host,req,peer=askbridge._peer_pid(writer))
            writer.write((json.dumps(res)+'\n').encode()); await writer.drain(); writer.close(); await writer.wait_closed()
        server=await asyncio.start_unix_server(serve,path=str(sock))
        code="import socket,sys,time;time.sleep(.1);s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall((sys.argv[2]+'\\n').encode());print(s.recv(65536).decode());s.close()"
        req=json.dumps({'args':{'action':'status','id':o.id},'nonce':secrets.token_hex(16)})
        try:
            p=subprocess.Popen([sys.executable,'-c',code,str(sock),req],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.ag.toolbox=NS(proc=p)
            out,err=await asyncio.to_thread(p.communicate,timeout=5)
            self.assertEqual(p.returncode,0,err); self.assertEqual(json.loads(out)['result'],'ok')
            q=subprocess.Popen([sys.executable,'-c',code,str(sock),req],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            out,err=await asyncio.to_thread(q.communicate,timeout=5)
            self.assertEqual(json.loads(out)['result'],'refused')
        finally: server.close(); await server.wait_closed()

    async def test_replay_survives_new_host_object(self):
        o=self.order(); req={'args':{'action':'send','id':o.id,'text':'once'},'nonce':secrets.token_hex(16)}
        self.assertEqual((await order_host.handle(self.host,req,internal=self.ag))['result'],'ok')
        fresh=NS(agent=self.ag,stopped=self.host.stopped,st=self.host.st)
        self.assertEqual((await order_host.handle(fresh,req,internal=self.ag))['why'],'replay')
        self.assertEqual(len(o.inbox_all()),1)

    async def test_broken_replay_ledger_fails_closed(self):
        import hashlib
        parent={'kind':'owner_turn','session':self.sid,'harness':'core'}
        path=orders.root(self.host.st.root)/'_requests'/(hashlib.sha256(json.dumps(parent,sort_keys=True).encode()).hexdigest()+'.jsonl')
        path.parent.mkdir(parents=True); path.write_text('{truncated')
        self.assertEqual((await self.call({'action':'status'}))['why'],'replay_ledger')

    async def test_current_peer_uid_mismatch_is_denied(self):
        proc=NS(pid=os.getpid(),poll=lambda:None); self.ag.toolbox=NS(proc=proc)
        with mock.patch.object(order_host.process_evidence,'uid',return_value=os.getuid()+1):
            with self.assertRaises(orders.OrderError): order_host.authority(self.host,peer=os.getpid())

    async def test_order_view_bounds_and_ownership(self):
        from agentj.core import order_view
        o=self.order(); orders._write(o.path/'report.md',HEAD)
        o.gate('Preference?', ['A','B'], 'B')
        projected=order_view.project(self.host,'order:'+o.id,{'src':'dispatch'})
        self.assertEqual(projected['order']['head']['done'],'1/1')
        self.assertEqual(projected['order']['gates'][0]['default'],'B')
        self.assertIn(o.id,projected['order']['origin'][0])
        self.sid='other'
        self.assertEqual(order_view.project(self.host,'order:'+o.id,{}),{})

    async def test_existing_codex_denial_through_real_order_ask_socket(self):
        from agentj.agent_codex import CodexAgent
        o=self.order(); orders._write(o.path/'.ask_token','synthetic-claim')
        orders._write(o.path/'pid',str(os.getpid()))
        asked=[]
        async def deny(tool,inp,gone=None,**kw):
            asked.append((tool,inp,kw)); return {'behavior':'deny'}
        server=await askbridge.Server(deny,self.host.st.root).start()
        client=askbridge.Client(o,self.host.st.root)
        try:
            self.assertTrue(await client.connect())
            async def ask(tool,inp,gone=None,**kw): return await client(tool,inp,None)
            native=CodexAgent.__new__(CodexAgent)
            native.pending={}; native.research=False; native.cfg={'_workflow_ceo':True}
            native.host=NS(ask=ask); answers=[]
            native._answer=lambda rid,ans:answers.append((rid,ans))
            await native._decide(53,'Bash',{'command':'rm result.txt'},{'decision':'accept'},{'decision':'decline'},False)
            self.assertEqual(answers,[(53,{'decision':'decline'})])
            self.assertEqual(len(asked),1)
            self.assertFalse(asked[0][2]['batch'])
            self.assertIn(o.id,asked[0][2]['origin'])
        finally:
            await client.close(); await server.close()

    async def test_unrelated_live_process_and_missing_peer_rejected(self):
        p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
        try:
            self.ag.toolbox=NS(proc=p)
            for peer in (None,os.getpid()):
                with self.assertRaises(orders.OrderError): order_host.authority(self.host,peer=peer)
        finally: p.terminate(); p.wait(timeout=5)

    async def test_request_cannot_supply_origin_outside_args(self):
        r=await order_host.handle(self.host,{'args':{'action':'status'},'nonce':secrets.token_hex(16),
                                           'origin':{'role':'owner'}},internal=self.ag)
        self.assertEqual(r['why'],'shape')

    async def test_global_pause_blocks_launch_resume_and_drain(self):
        o=self.order()
        dispatcher.set_paused(self.host.st.root,True)
        with self.assertRaises(orders.OrderError): dispatcher.launch(o,st_root=self.host.st.root)
        with self.assertRaises(orders.OrderError): dispatcher.resume(o,'must not append',st_root=self.host.st.root)
        self.assertEqual(o.inbox_all(),[])
        self.assertEqual(dispatcher.drain(self.host.st.root),[])
        await order_host.pause_all(self.host)
        self.assertEqual(o.state(),'stopped')
        dispatcher.set_paused(self.host.st.root,False)
        self.assertFalse(dispatcher.paused(self.host.st.root))

    async def test_pause_all_confirms_real_owned_process_exit(self):
        script=self.root/'sleep.py'
        script.write_text("import os,sys,time,pathlib\np=pathlib.Path(os.environ['AGENTJ_STATE_DIR'])/'orders'/sys.argv[1]\n(p/'pid').write_text(str(os.getpid()))\nopen(p/'events.jsonl','a').write('{\"ev\":\"started\"}\\n')\ntime.sleep(30)\n")
        o=self.order()
        with mock.patch.object(dispatcher,'ceo_argv',lambda o:[sys.executable,str(script),o.id]),mock.patch.object(dispatcher,'STOP_GRACE',.05):
            await asyncio.to_thread(dispatcher.launch,o,st_root=self.host.st.root)
            try:
                self.assertTrue(await asyncio.to_thread(dispatcher.wait_started,o,5))
                await order_host.pause_all(self.host)
                self.assertTrue(dispatcher.paused(self.host.st.root))
                self.assertEqual(o.state(),'stopped')
                self.assertFalse(dispatcher.alive(o))
                self.assertTrue(json.loads((o.path/'exit-proof.json').read_text())['quiet'])
            finally: await asyncio.to_thread(dispatcher.stop,o,grace=0)

class PresetCompatibility(unittest.TestCase):
    def test_retired_provider_name_is_current_token_plan_alias_without_old_key(self):
        from agentj.core import presets
        canonical=presets.resolve('bailian-token-plan',environ={})
        alias=presets.resolve('bailian-coding-plan',environ={})
        self.assertEqual(alias.key_env,'BAILIAN_TOKEN_PLAN_API_KEY')
        self.assertEqual((alias.base_url,alias.model,alias.fallback),(canonical.base_url,canonical.model,canonical.fallback))
        self.assertEqual((alias.protocol,alias.stream),('anthropic-messages',True))
        self.assertFalse(presets.providers()['bailian-coding-plan']['verified'])
        self.assertIsNone(alias.key({'BAILIAN_CODING_PLAN_API_KEY':'synthetic-retired'}))
