"""Offline native wire peers, actual adapters and subprocesses; no paid-model claim."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from agentj.core import orders, askbridge, native_order
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent

BRIEF = {'goal':'offline','scope':{'write':['result.txt']},'verify':['offline'],'timebox':1,'report':'head'}
HEAD = '完成：1/1\nready：是\n需要上级：无\nblocked-by: -\nVERDICT: ok — offline native'
CODEX = r'''
import sys,json
head=sys.argv[1]
def emit(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method'); rid=m.get('id')
 if method=='initialize': r={'userAgent':'codex/synthetic'}
 elif method=='config/read': r={'config':{'sandbox_mode':'workspace-write'}}
 elif method=='thread/start': r={'thread':{'id':'order-only'},'sandbox':{'type':'workspaceWrite','writableRoots':[]}}
 elif method=='turn/start':
  emit({'id':rid,'result':{'turn':{'id':'t1'}}})
  emit({'method':'turn/started','params':{'threadId':'order-only','turn':{'id':'t1'}}})
  emit({'id':900,'method':'item/commandExecution/requestApproval','params':{'threadId':'order-only','turnId':'t1','command':'cat result.txt'}})
  continue
 elif rid==900 and 'result' in m:
  answer=m['result']['decision']
  emit({'method':'thread/tokenUsage/updated','params':{'threadId':'order-only','tokenUsage':{'total':{'inputTokens':10,'outputTokens':3},'last':{'inputTokens':10,'outputTokens':3}}}})
  emit({'method':'item/completed','params':{'threadId':'order-only','turnId':'t1','item':{'id':'msg','type':'agentMessage','text':head+' '+answer}}})
  emit({'method':'turn/completed','params':{'threadId':'order-only','turn':{'id':'t1','status':'completed'}}})
  continue
 elif method=='thread/inject_items': r={}
 elif method=='turn/interrupt': r={}
 else: r={}
 if method and rid is not None: emit({'id':rid,'result':r})
'''
CLAUDE = r'''
import os,sys,json,subprocess
head=sys.argv[1]
perm=subprocess.Popen([sys.executable,'-P','-c',"import sys,runpy;sys.path.insert(0,'@HOST_SOURCE@');runpy.run_module('agentj.permtool',run_name='__main__')"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
def mcp(req):
 perm.stdin.write(json.dumps(req)+'\n'); perm.stdin.flush()
 return json.loads(perm.stdout.readline())
assert mcp({'id':0,'method':'initialize','params':{}})['result']['serverInfo']['name']=='agentj'
def emit(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 if m.get('type')=='control_request':
  emit({'type':'control_response','response':{'request_id':m['request_id'],'subtype':'success','response':{}}}); continue
 if m.get('type')!='user': continue
 emit({'type':'system','subtype':'init','session_id':'order-only','model':'synthetic-claimed-model'})
 result=mcp({'id':1,'method':'tools/call','params':{'name':'approve','arguments':{'tool_name':'Bash','input':{'command':'cat result.txt'}}}})
 answer=json.loads(result['result']['content'][0]['text'])['behavior']
 emit({'type':'assistant','message':{'content':[{'type':'text','text':head+' '+answer}]}})
 emit({'type':'result','subtype':'success','usage':{'input_tokens':10,'output_tokens':3}})
'''

CLAUDE = CLAUDE.replace('@HOST_SOURCE@', str(Path(__file__).resolve().parents[1]))

class Native(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.project=self.root/'project'; self.project.mkdir()
        self.state=self.root/'state'; self.asked=[]
    def order(self, kind):
        o=orders.create(project=str(self.project), prompt_text='synthetic', title='native', brief=BRIEF,
                        harness=kind, model='host-frozen-model', fence=False, st_root=self.state)
        from agentj import provenance
        o.event('origin', source=provenance.phone())
        (o.path/'pid').write_text(str(os.getpid())); orders._write(o.path/'.ask_token','offline-token')
        return o
    async def exercise(self, kind, answer):
        o=self.order(kind)
        script=self.root/(kind+'.py'); script.write_text(CLAUDE if kind=='claude' else CODEX)
        async def ask(tool, inp, gone=None, **kw):
            self.asked.append((tool,inp,kw)); return {'behavior':answer}
        server=await askbridge.Server(ask,self.state).start(); client=askbridge.Client(o,self.state,timeout=2)
        self.assertTrue(await client.connect())
        cls=ClaudeAgent if kind=='claude' else CodexAgent
        try:
            with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(cls,'argv',lambda *a,**kw:[sys.executable,str(script),HEAD]):
                r=await asyncio.wait_for(native_order.run(o,client,self.state,'full prompt'),15)
            self.assertEqual(r['code'],0,r)
            self.assertIn(('allow' if kind=='claude' else 'accept') if answer=='allow' else ('deny' if kind=='claude' else 'decline'),r['text'])
            self.assertEqual(r['usage'],{'input':10,'output':3})
            self.assertEqual(len(self.asked),1)
            self.assertIs(self.asked[0][2]['batch'],False)
            self.assertIn(o.id,self.asked[0][2]['origin'])
            self.assertNotIn('synthetic-claimed-model',self.asked[0][2]['origin'])
        finally:
            await client.close(); await server.close()
    async def test_codex_real_adapter_native_decline(self): await self.exercise('codex','deny')
    async def test_codex_real_adapter_native_accept(self): await self.exercise('codex','allow')
    async def test_claude_real_adapter_claim_and_deny(self): await self.exercise('claude','deny')
    async def test_claude_real_adapter_claim_and_allow(self): await self.exercise('claude','allow')

    async def test_dispatcher_guardian_real_ceo_resume_all_append_and_meter(self):
        import json5  # prerequisite before launching a child that imports the same dependency
        from agentj.core import dispatcher
        for kind in ('claude','codex'):
            with self.subTest(kind=kind):
                o=self.order(kind)
                script=self.root/(kind+'-lifecycle.py')
                trace=self.root/(kind+'-input.jsonl')
                wire = CLAUDE if kind=='claude' else CODEX
                wire = wire.replace(' m=json.loads(line)', ' with open(sys.argv[2], \'a\') as trace: trace.write(line)\n m=json.loads(line)')
                script.write_text(wire)
                wrapper=self.root/(kind+'-ceo.py')
                wrapper.write_text('''import sys,os,json
if os.environ.get('QA53_JSON_ONLY_DIAGNOSTIC')=='1': sys.modules['json5']=json
from unittest import mock
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from agentj.core import ceo
cls=ClaudeAgent if sys.argv[2]=='claude' else CodexAgent
with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(cls,'argv',lambda *a,**kw:[sys.executable,sys.argv[3],sys.argv[4],sys.argv[5]]):
 raise SystemExit(ceo.main([sys.argv[1]]))
''')
                calls=[]
                async def ask(tool, inp, gone=None, **kw):
                    calls.append(kw); return {'behavior':'deny'}
                server=await askbridge.Server(ask,self.state).start()
                env=dict(os.environ,AGENTJ_STATE_DIR=str(self.state),AGENTJ_ORDER_LAUNCHER='proc',AGENTJ_CORE='1')
                def argv(order): return [sys.executable,str(wrapper),order.id,kind,str(script),HEAD,str(trace)]
                try:
                    with mock.patch.object(dispatcher,'ceo_argv',argv):
                        await asyncio.to_thread(dispatcher.launch,o,environ=env,st_root=self.state)
                        until=asyncio.get_running_loop().time()+15
                        while not o.result() and asyncio.get_running_loop().time()<until: await asyncio.sleep(.02)
                        self.assertIsNotNone(o.result())
                        self.assertEqual(o.result()['exit_code'],0,o.events())
                        self.assertEqual(o.result()['tokens'],{'input':10,'output':3})
                        # stop also observes the original guardian's tree receipt before resume.
                        await asyncio.to_thread(dispatcher.stop,o)
                        o.send('addition one'); o.send('addition two')
                        first=o.report()
                        await asyncio.to_thread(dispatcher.resume,o,environ=env,st_root=self.state)
                        until=asyncio.get_running_loop().time()+15
                        while not o.result() and asyncio.get_running_loop().time()<until: await asyncio.sleep(.02)
                        self.assertEqual(o.result()['exit_code'],0,o.events())
                        await asyncio.to_thread(dispatcher.stop,o)
                        self.assertEqual(o.inbox_all(),[(1,'addition one'),(2,'addition two')])
                        self.assertEqual(len(list((o.path/'history-reports').glob('*.md'))),1)
                        self.assertEqual(next((o.path/'history-reports').glob('*.md')).read_text(),first)
                        self.assertEqual(len(calls),1, 'order-wide denial survives resume')
                        self.assertIn('addition one',trace.read_text())
                        self.assertIn('addition two',trace.read_text())
                        self.assertIn('Previous report',trace.read_text())
                        self.assertTrue(all(c['batch'] is False and o.id in c['origin'] for c in calls))
                finally:
                    await asyncio.to_thread(dispatcher.stop,o)
                    await server.close()

    async def test_core_host_freezes_native_model_and_fixed_harness_enums(self):
        from types import SimpleNamespace as NS
        from agentj import provenance
        from agentj.core import dispatcher, order_host
        ag=NS(kind='core',native_source=provenance.phone(),cfg={'dir':str(self.project),'fence':True,'model':'core-only'})
        host=NS(agent=ag,stopped=lambda:False,st=NS(root=self.state,agent_session=lambda k:'current',log=lambda *a,**k:None))
        import secrets
        async def call(args):
            return await order_host.handle(host,{'args':args,'nonce':secrets.token_hex(16)},internal=ag)
        with mock.patch.dict(os.environ,{'AGENTJ_CORE':'1'}), mock.patch.object(dispatcher,'launch',return_value='queued') as launch:
            for kind in ('claude','codex'):
                r=await call({'action':'new','project':str(self.project),'prompt':'go','brief':BRIEF,'harness':kind})
                self.assertEqual(r['result'],'ok',r)
                o=orders.get(r['id'],self.state)
                self.assertEqual(o.meta['harness'],kind)
                self.assertNotEqual(o.meta['model'],'core-only')
                self.assertTrue(o.meta['model'])
                self.assertIs(o.meta['fence'],True)
            self.assertEqual(launch.call_count,2)
            self.assertEqual((await call({'action':'new','project':str(self.project),'prompt':'go','brief':BRIEF,'harness':'arbitrary'}))['why'],'bad_field')
            self.assertEqual((await call({'action':'new','project':str(self.project),'prompt':'go','brief':BRIEF,'harness':'codex','model':'forged'}))['why'],'shape')

    async def test_late_approval_stop_generation_timeout_and_cross_order_deny(self):
        from agentj.core import dispatcher
        o=self.order('codex')
        entered=asyncio.Event(); release=asyncio.Event()
        async def pending(*a): entered.set(); await release.wait(); return {'behavior':'allow'}
        h=native_order.Host(o,pending,self.state)
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c','import time;time.sleep(20)')
        from types import SimpleNamespace as NS
        h.agent=NS(proc=proc)
        try:
            for mutation in ('stop','generation','pause','process','timeout'):
                entered.clear(); release.clear(); h.closed=False; h.generation='current'; h.ask_ttl=.1
                o.set_control(None); dispatcher.set_paused(self.state,False); h.agent.proc=proc
                task=asyncio.create_task(h.ask('Bash',{'command':'cat '+mutation}))
                await entered.wait()
                if mutation=='stop': o.set_control('stop')
                if mutation=='generation': h.generation='wrong'
                if mutation=='pause': dispatcher.set_paused(self.state,True)
                if mutation=='process': h.agent.proc=NS(returncode=None)
                if mutation!='timeout': release.set()
                self.assertEqual((await task)['behavior'],'deny',mutation)
            dispatcher.set_paused(self.state,False); o.set_control(None)
            async def broken(*args): raise ConnectionError('offline transport lost')
            h.client=broken
            self.assertEqual((await h.ask('Bash',{'command':'cat broken'}))['behavior'],'deny')
            # Real ask bridge refuses an order id paired with another order's token.
            server=await askbridge.Server(lambda *a: None,self.state).start()
            other=self.order('claude'); orders._write(other.path/'.ask_token','other-token')
            try:
                r,w=await asyncio.open_unix_connection(str(askbridge.sock_path(self.state)))
                w.write((json.dumps({'t':'hello','order':other.id,'token':'offline-token'})+'\n').encode()); await w.drain()
                self.assertEqual(json.loads(await r.readline())['t'],'refused')
                w.close(); await w.wait_closed()
            finally: await server.close()
        finally:
            proc.terminate(); await proc.wait()

    async def test_started_adapter_without_approval_chain_denies_native(self):
        o=self.order('codex'); script=self.root/'unlinked.py'; script.write_text(CODEX)
        client=askbridge.Client(o,self.state)
        with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(CodexAgent,'argv',lambda *a:[sys.executable,str(script),HEAD]):
            r=await asyncio.wait_for(native_order.run(o,client,self.state,'go'),10)
        self.assertEqual(r['code'],0)
        self.assertIn('decline',r['text'])
        self.assertTrue(o.denied())

    async def test_wrong_native_thread_cannot_borrow_live_order(self):
        o=self.order('codex'); script=self.root/'wrong-thread.py'
        script.write_text(CODEX.replace("'params':{'threadId':'order-only','turnId':'t1','command'", "'params':{'threadId':'other-order','turnId':'t1','command'"))
        async def ask(*a): self.fail('wrong thread reached approval')
        with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(CodexAgent,'argv',lambda *a:[sys.executable,str(script),HEAD]):
            r=await asyncio.wait_for(native_order.run(o,ask,self.state,'go'),10)
        self.assertEqual(r['code'],0)
        self.assertIn('decline',r['text'])

    async def test_native_budget_carries_history_and_current_meter(self):
        o=self.order('codex')
        m=o.meta; m['budget']['tokens']=12; orders._write(o.path/'order.json',json.dumps(m))
        script=self.root/'meter.py'; script.write_text(CODEX)
        async def deny(*a): return {'behavior':'deny'}
        with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(CodexAgent,'argv',lambda *a:[sys.executable,str(script),HEAD]):
            r=await asyncio.wait_for(native_order.run(o,deny,self.state,'go'),10)
        self.assertEqual(r['usage'],{'input':10,'output':3})
        self.assertEqual(r['reason'],'budget')
        orders._append(o.path/'history.jsonl',{'wall_s':60,'tokens':{'input':10,'output':3}})
        with mock.patch('agentj.agent._bin') as binary:
            r=await native_order.run(o,deny,self.state,'resume')
        self.assertEqual(r['reason'],'budget')
        binary.assert_not_called()

    async def test_live_native_interrupt_via_dispatcher_confirms_guardian_exit(self):
        import json5  # prerequisite; a missing runner dependency is an error, never a skip
        from agentj.core import dispatcher
        for kind in ('claude','codex'):
            with self.subTest(kind=kind):
                o=self.order(kind); script=self.root/(kind+'-stop.py'); script.write_text(CLAUDE if kind=='claude' else CODEX)
                wrapper=self.root/(kind+'-stop-ceo.py')
                wrapper.write_text('''import sys,os,json
if os.environ.get('QA53_JSON_ONLY_DIAGNOSTIC')=='1': sys.modules['json5']=json
from unittest import mock
from agentj.agent import ClaudeAgent
from agentj.agent_codex import CodexAgent
from agentj.core import ceo
cls=ClaudeAgent if sys.argv[2]=='claude' else CodexAgent
with mock.patch('agentj.agent._bin',return_value=sys.executable), mock.patch.object(cls,'argv',lambda *a,**kw:[sys.executable,sys.argv[3],sys.argv[4]]):
 raise SystemExit(ceo.main([sys.argv[1]]))
''')
                entered=asyncio.Event()
                async def hold(*a,**kw): entered.set(); await asyncio.Event().wait()
                server=await askbridge.Server(hold,self.state).start()
                env=dict(os.environ,AGENTJ_STATE_DIR=str(self.state),AGENTJ_ORDER_LAUNCHER='proc',AGENTJ_CORE='1')
                try:
                    with mock.patch.object(dispatcher,'ceo_argv',lambda order:[sys.executable,str(wrapper),order.id,kind,str(script),HEAD]):
                        await asyncio.to_thread(dispatcher.launch,o,environ=env,st_root=self.state)
                        await asyncio.wait_for(entered.wait(),10)
                        await asyncio.to_thread(dispatcher.stop,o)
                        self.assertEqual(o.result()['state'],'stopped',o.events())
                        proof=json.loads((o.path/'exit-proof.json').read_text())
                        self.assertIs(proof['quiet'],True)
                        self.assertFalse(dispatcher.alive(o))
                finally:
                    await asyncio.to_thread(dispatcher.stop,o)
                    await server.close()

    async def test_crashed_generation_budget_ledger_cannot_reset_on_resume(self):
        o=self.order('codex')
        h=native_order.Host(o,None,self.state)
        await h.start()
        # Simulate loss before a confirmed close; a different Host must not erase it.
        h.srv.close(); await h.srv.wait_closed()
        with self.assertRaises(orders.OrderError) as err:
            native_order.Host(o,None,self.state)
        self.assertEqual(err.exception.code,'native_budget_unconfirmed')
        o.path.joinpath('native-budget.json').write_text('{broken')
        with self.assertRaises(orders.OrderError) as err:
            native_order.Host(o,None,self.state)
        self.assertEqual(err.exception.code,'native_budget_ledger')
