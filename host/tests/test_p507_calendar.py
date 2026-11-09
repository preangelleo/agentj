"""P503: isolated regressions for the stolen paired-device trust boundary."""
import _hermetic  # noqa: F401
import asyncio
import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agentj import mail, passkey, serve, wire, elevate
from agentj.noise import Keypair
from agentj.state import State
from test_p57_passkey import Authenticator, pk_record


class HostExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_l1 import _ready, Phone
        from _p541_device import _owner_host as _host
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st=State(pathlib.Path(self.tmp.name));self.st.init()
        self.sent=[];self.h=_host(self.st,self.sent);self.ph=Phone(self.st)
        self.auth=Authenticator();self.st.set_passkey(self.ph.pub,pk_record(self.auth))
        self.session=_ready(self.h,self.ph);self.session.p33=True

class CalendarFinalExecution(HostExecution):
    def calendar_fixture(self, op):
        from agentj import jcards, jcards_cal as cal
        from test_p428_calendar_sync import GOOGLE, T, Fake
        jcards._write_json(self.st.root, ('cards-data', 'calendar'), 'calendar.json',
                           {'schema':1, 'events':[], 'subscriptions':{'google':dict(GOOGLE)}})
        remote = Fake()
        out = cal.change(self.st.root, 'add', to='google', **T)
        ident = out['events'][0]['id']
        if op != 'create':
            def linked(doc):
                ev = doc['events'][0]
                ev.update(rid='fixture-remote', etag='"e1"', base='old')
                doc['outbox'] = []
            cal.transaction(self.st.root, linked)
            remote.put('fixture-remote')
            cal.change(self.st.root, 'delete' if op == 'delete' else 'edit', ident, **({} if op == 'delete' else {'title':'Moved'}))
        queued = cal.load(self.st.root)[0]['outbox'][0]
        return remote, queued

    async def native_calendar(self, queued, *, host_sync=False, drift='none', mutate=None, valid=True, guest=False, research=False, replay=False):
        """Actual kernel caller→elevate→Host ordinary decision; fake vendor only."""
        import os, sys, signal, contextlib
        from types import SimpleNamespace as NS
        from agentj import codex_procs, provenance, approval_review
        host, old_agent, old_cfg = self.h, self.h.agent, self.h.agent_cfg
        original_ask = host.ask
        sent_before=len(self.sent)
        observed=[]
        async def observe(tool, inp, *args, **kwargs):
            answer = await original_ask(tool, inp, *args, **kwargs)
            if tool == 'agentj calendar':
                observed.append({'input':json.loads(json.dumps(inp)), 'answer':dict(answer)})
                if mutate is not None and answer.get('behavior') == 'allow': mutate()
            return answer
        host.ask=observe
        await host.elevate.start()
        script = r'''import json,sys
from unittest.mock import patch
from agentj import jcards_cal as cal,jcards_cal_remote as remote,google
from agentj.state import State
from test_p428_calendar_sync import Fake
st=State(); data=json.loads(sys.argv[1]); queued=data['queued']; op=queued['op']; adapter=Fake()
if op!='create': adapter.put('fixture-remote')
if not data['host_sync']:
    result=remote._push(st.root,'google',adapter,queued)
    second=remote._push(st.root,'google',adapter,queued) if data['replay'] else None
    calls=adapter.calls
else:
    dest=cal.load(st.root)[0]['subscriptions']['google']['calendar']; calls=[]
    def gog(st,argv,timeout):
        assert argv[2]==dest
        if argv[1]=='events':
            if data['drift']=='pull': cal.transaction(st.root,lambda d:d['subscriptions']['google'].update(calendar='other@example.com'))
            if op!='create': return 0,[{'id':'fixture-remote','etag':'"e1"','summary':'Meeting',
                'start':{'dateTime':'2026-10-20T09:30:00+00:00'},'end':{'dateTime':'2026-10-20T10:30:00+00:00'}}]
            return 0,[]
        calls.append(argv[1]); return 0,{'id':'fixture-remote','etag':'"e2"'}
    with patch.object(google,'gog_calendar',side_effect=gog): result=remote.host_sync(st,'google')
    second=None
print(json.dumps({'result':result,'calls':calls,'second':second}))
'''
        anchor="import subprocess,sys;sys.stdin.readline();sys.exit(subprocess.call([sys.executable,'-c',sys.argv[1],sys.argv[2]]))"
        env={**os.environ,'AGENTJ_STATE_DIR':str(self.st.root),
             'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1])+os.pathsep+str(pathlib.Path(__file__).resolve().parent)}
        proc=None
        try:
            proc=await asyncio.create_subprocess_exec(sys.executable,'-c',anchor,script,
                json.dumps({'queued':queued,'host_sync':host_sync,'drift':drift,'replay':replay}),env=env,
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            host.agent_cfg={'kind':'claude','dir':str(self.st.root)}
            host.agent=NS(host=host,kind='claude',cfg=host.agent_cfg,phone_turn=False,research=research,
                native_source=provenance.telegram_group(-1,2,'proxy',1) if guest else provenance.system(),
                session={'sessionId':'calendar-kernel','pid':proc.pid,'procStart':codex_procs.start_token(proc.pid)})
            if not valid: host.agent.session['procStart']='wrong-generation'
            proc.stdin.write(b'go\n');await proc.stdin.drain()
            out,err=await asyncio.wait_for(proc.communicate(),8)
            self.assertEqual(proc.returncode,0,err.decode())
            got=json.loads(out.decode().splitlines()[-1])
            self.assertFalse(host.asks)
            self.assertFalse(any(obj.get('t')=='ask' for _,obj in self.sent[sent_before:]))
            for item in observed:
                self.assertFalse(set(item['answer']) & {'grant_info','device','sig','rid','uv'})
                self.assertEqual(item['input']['operation'],queued['op'])
            return got,observed
        finally:
            host.ask=original_ask
            host.agent,host.agent_cfg=old_agent,old_cfg
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    if os.getpgid(proc.pid)==proc.pid:os.killpg(proc.pid,signal.SIGKILL)
                await proc.wait()
            await host.elevate.stop()
            if host.elevate.bg: await asyncio.wait_for(asyncio.gather(*list(host.elevate.bg)),2)

    async def listed_uv(self, *, without_key=False):
        """Keep missing/bad-UV controls on a genuine closed-list invocation."""
        from _p541_device import Phone as V2Phone
        task=asyncio.create_task(self.h.ask('agentj secret request', {'name':'calendar-fixture-only'}))
        try:
            for _ in range(100):
                if self.h.asks or task.done():break
                await asyncio.sleep(.002)
            self.assertFalse(task.done())
            a=next(iter(self.h.asks.values()));card=self.h._ask_msg(a,self.session)
            self.assertTrue(card['passkey_required'])
            self.ph.auth=self.auth
            obj=V2Phone.answer(self.ph,card,True);obj.pop('fa',None)
            await self.h._answer(self.session,obj)
            self.assertFalse(a.fut.done())
            if without_key:
                await self.h._answer(self.session,V2Phone.answer(self.ph,card,False))
                self.assertEqual((await asyncio.wait_for(task,3))['behavior'],'deny')
            else:
                ch=passkey.elevate_challenge(self.h.channel,self.ph.did,a.rid,a.pk_kind,a.rid,obj['envelope_sha256'])
                bad=self.auth.assert_(ch,b'0',nonce=b'0',flags=1)
                await self.h._answer(self.session,{**obj,'fa':bad})
                self.assertFalse(a.fut.done())
                good={**obj,'fa':self.auth.assert_(ch,b'0',nonce=b'0')}
                await self.h._answer(self.session,good)
                self.assertEqual((await asyncio.wait_for(task,3))['behavior'],'allow')
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def test_offline_background_create_update_delete_cannot_execute(self):
        from agentj import jcards_cal_remote as remote
        for op in ('create','update','delete'):
            adapter,queued=self.calendar_fixture(op)
            await asyncio.to_thread(remote._push,self.st.root,'google',adapter,queued)
            self.assertFalse(adapter.calls,op)
            # Actual socket caller without the live kernel invocation, or an
            # actual restricted guest/research invocation, still has no effect.
            for valid,guest,research in ((False,False,False),(True,True,False),(True,False,True)):
                got,seen=await self.native_calendar(queued,valid=valid,guest=guest,research=research)
                self.assertEqual(got['result'],'approval_required')
                self.assertEqual(got['calls'],[])
                self.assertTrue(seen)
                self.assertEqual(seen[-1]['answer']['reason'],'application_ingress_unverified')

    async def test_each_final_calendar_effect_needs_its_own_approval(self):
        for op in ('create','update','delete'):
            adapter,queued=self.calendar_fixture(op)
            got,seen=await self.native_calendar(queued,replay=True)
            self.assertEqual(got['result'],None)
            self.assertEqual(got['calls'],[op])
            # An already-synced update is a no-op before authorization;
            # create/delete replays fail the consumed outbox binding.
            self.assertEqual(got['second'],'skip' if op=='update' else 'approval_required')
            self.assertTrue(seen)
            self.assertEqual(seen[0]['answer']['behavior'],'allow')
            self.assertEqual(seen[0]['input']['operation'],op)
            # Calendar delete itself is ordinary. Missing/invalid UV remains a
            # true negative on an actual secret-controller list invocation.
            await self.listed_uv()

    async def test_final_calendar_rechecks_queue_content_and_destination_after_approval(self):
        from agentj import jcards_cal as cal
        for change in ('content','destination','queue'):
            adapter,queued=self.calendar_fixture('create')
            def mutate():
                def update(doc):
                    if change=='content':doc['events'][0]['title']='Changed during approval'
                    elif change=='destination':doc['subscriptions']['google']['calendar']='other@example.com'
                    else:doc['outbox']=[]
                cal.transaction(self.st.root,update)
            # Observe and mutate after the real ordinary decision inside the
            # authenticated frame; never stub approval or pass authorize=True.
            got,seen=await self.native_calendar(queued,mutate=mutate)
            self.assertEqual(got['result'],'approval_required',change)
            self.assertFalse(got['calls'],change)
            self.assertEqual(seen[0]['answer']['behavior'],'allow')
            self.assertEqual(seen[0]['input']['operation'],'create')

    async def test_no_passkey_allows_reversible_but_never_a_remote_delete(self):
        from test_l1 import _ready,Phone
        from _p541_device import _owner_host
        with tempfile.TemporaryDirectory() as d:
            previous=(self.st,self.h,self.ph,self.session,self.sent)
            self.st=State(pathlib.Path(d));self.st.init()
            self.sent=[];self.h=_owner_host(self.st,self.sent);self.ph=Phone(self.st)
            self.session=_ready(self.h,self.ph);self.session.p33=True
            try:
                for op in ('create','update','delete'):
                    adapter,queued=self.calendar_fixture(op)
                    got,seen=await self.native_calendar(queued)
                    self.assertEqual(got['calls'],[op])
                    self.assertEqual(got['result'],None)
                    self.assertEqual(seen[0]['answer']['behavior'],'allow')
                    await self.listed_uv(without_key=True)
            finally:self.st,self.h,self.ph,self.session,self.sent=previous

    async def test_host_sync_binds_actual_google_adapter_destination_across_pull_and_uv(self):
        from agentj import jcards_cal as cal
        for op in ('create','update','delete'):
            for drift in ('pull','approval','none'):
                with self.subTest(op=op,drift=drift):
                    _,queued=self.calendar_fixture(op)
                    dest=cal.load(self.st.root)[0]['subscriptions']['google']['calendar']
                    def move():
                        cal.transaction(self.st.root,lambda d:d['subscriptions']['google'].update(calendar='other@example.com'))
                    got,seen=await self.native_calendar(queued,host_sync=True,drift=drift,
                                                       mutate=move if drift=='approval' else None)
                    result=got['result']
                    if drift=='pull':
                        self.assertEqual(result.get('why'),'approval_required',result)
                        self.assertFalse(seen)
                    else:
                        self.assertTrue(seen)
                        self.assertEqual(seen[0]['input']['subscription']['calendar'],dest)
                        self.assertEqual(result['pushed'],1 if drift=='none' else 0,result)
                    self.assertEqual(got['calls'],[op] if drift=='none' else [])

    async def test_oversized_calendar_summary_uses_calendar_bilingual_copy(self):
        for lang in ('zh','en'):
            self.h.lang=lang
            result=await self.h.ask('agentj calendar',{'note':'x'*24001},batch=False,require_passkey=True)
            self.assertEqual((result['behavior'],result['reason']),('deny','application_ingress_unverified'))
            self.assertNotIn('x'*100,result['message'])
            self.assertFalse(self.h.asks)
