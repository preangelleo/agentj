"""Typed secret-plan metadata: actual Host/Noise/signature/UV and original CLI transfer."""
import _hermetic
import asyncio
import copy
import base64
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
from agentj import approvals, approval_review, owner_grant as og, serve, wire, action_policy
from agentj.noise import CipherState
import test_p574_owner_grant as fixture
from _p541_device import Phone as V2Phone
from test_p513b_summary import outbound_card


class SecretPlanDisplay(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config=tempfile.TemporaryDirectory(prefix='aj-secret-plan-config-')
        self.addCleanup(self.config.cleanup)
        env=mock.patch.dict(os.environ,{'XDG_CONFIG_HOME':self.config.name})
        env.start();self.addCleanup(env.stop)
        self.f=fixture.HostFlows();await self.f.asyncSetUp()
        self.h,self.x,self.st=self.f.h,self.f.svc.xfer,self.f.st
        self.h.ask_ttl=2
        self.sent=self.f.sent
        receive={}
        for session in (self.f.s,self.f.other_s):
            key=os.urandom(32);session.send=CipherState(key);session.p33=True
            receive[session.cid]=(CipherState(key),wire.Defrag())
        async def relay_sink(op,cid,payload=b''):
            if op!=wire.OP_DATA:return
            cipher,fragments=receive[cid]
            self.assertEqual(payload[0],wire.DATA)
            frame=fragments.feed(wire.unpad_json(cipher.decrypt(b'',payload[1:]),wire.MAX_JSON_P33))
            if frame is not None:self.sent.append((cid,frame))
        self.h._op=relay_sink
        self.h.send_app=serve.Host.send_app.__get__(self.h)
        self.tasks=[]
        self.compatibility_cases=[]

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.f.asyncTearDown();self.f.doCleanups()

    def protocol(self,version):
        from test_l1 import Phone as V1Phone
        self.f.phone.answer=types.MethodType((V2Phone if version==2 else V1Phone).answer,self.f.phone)

    async def open(self,lang='zh',version=2,plan_override=None,metadata_only=False):
        self.h.lang=lang;self.protocol(version);self.sent.clear()
        destination=Path(self.f.tmp.name)/('received-'+lang+'-'+str(version)+'.key')
        plan={'title':'Deployment','why':'Move the exact registered key','steps':[
            {'secret':'DEPLOY_KEY','from':'B','to':'self','dest':'file:'+str(destination)}]}
        if plan_override is not None:
            plan=plan_override;destination=Path(plan['steps'][0]['dest'][5:])
        if metadata_only:
            # Exercise real Host signing of schema-valid metadata only. The
            # synthetic /home directory intentionally does not exist, and no
            # file parser/read/transfer is invoked for these display cases.
            item=plan['steps'][0]
            otg=og.build(self.x.me,self.x._hosts([item['from']]),plan['title'],plan['why'],
                         [og.secret_step(item['secret'],item['from'],self.x.me,item['dest'],item['src'])],lang)
            async def sign_metadata():
                result=await self.x._sign(otg)
                return {'result':'signed','grant':otg['tid']} if 'proof' in result else result
            task=asyncio.create_task(sign_metadata())
        else:
            task=asyncio.create_task(self.x.cli({'op':'grant','plan':plan},self.f.tmp.name))
        self.tasks.append(task)
        card=await outbound_card(self,self.h,self.sent,task)
        ask=self.h.asks[card['id']]
        self.assertTrue(ask.require_passkey)
        self.assertFalse(destination.exists())
        self.assertNotIn(ask.tool_input['otg']['tid'],self.x._grants_out())
        self.assertEqual(card['summary'],card['envelope']['details'])
        self.assertEqual(card['envelope']['input_sha256'],approvals.input_digest(ask.tool_input))
        return task,ask,card,destination

    async def approve(self,ask,card):
        no_uv=self.f.phone.answer(card,True);no_uv.pop('fa')
        await self.h._app(self.f.s,no_uv)
        self.assertFalse(ask.fut.done());self.assertFalse(ask.uv_ok)
        forged=self.f.phone.answer(card,True,summary=card['summary']+' changed')
        await self.h._app(self.f.s,forged);self.assertFalse(ask.fut.done())
        answer=self.f.phone.answer(card,True)
        await self.h._app(self.f.s,answer)
        return answer

    async def test_v1_and_v2_bilingual_signed_metadata_stays_exact_and_live(self):
        for lang in ('zh','en'):
            for version in (1,2):
                with self.subTest(lang=lang,version=version):
                    task,ask,card,dest=await self.open(lang,version)
                    raw=copy.deepcopy(ask.tool_input);otg=raw['otg']
                    self.assertEqual(card['summary'],og.card_text(otg))
                    self.assertNotIn('<redacted>',card['summary'])
                    self.assertIn('DEPLOY_KEY',card['summary'])
                    self.assertIn(otg['hosts']['A'],card['summary'])
                    self.assertIn(otg['hosts']['B'],card['summary'])
                    self.assertIn('file:'+str(dest),card['summary'])
                    self.assertTrue(any(cid==self.f.other_s.cid and obj.get('t')=='ask' and obj.get('id')==ask.rid for cid,obj in self.sent))   # 0304 A1: fan-out
                    answer=await self.approve(ask,card)
                    result=await asyncio.wait_for(task,2)
                    self.assertEqual(result['result'],'signed')
                    record=self.x._grants_out()[result['grant']]
                    self.assertEqual(og.verify(record['otg'],record['proof'],[fixture.entry(self.f.phone.auth,self.f.phone.did)])['dev'],self.f.phone.did)
                    self.assertTrue(self.x.local_live(record));self.assertEqual(record['otg'],otg)
                    self.cache_compatibility(record)
                    self.assertEqual(ask.tool_input,raw);self.assertFalse(dest.exists())
                    self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')
                    before=len(self.x._grants_out())
                    await self.h._app(self.f.s,answer)
                    self.assertEqual(len(self.x._grants_out()),before)

    async def test_exact_proof_rejects_forged_display_input_source_and_target(self):
        task,ask,card,_=await self.open()
        await self.approve(ask,card);result=await asyncio.wait_for(task,2)
        rec=self.x._grants_out()[result['grant']];roster=[fixture.entry(self.f.phone.auth,self.f.phone.did)]
        for field in ('summary','input_sha256','source','target'):
            with self.subTest(field=field):
                proof=copy.deepcopy(rec['proof']);otg=copy.deepcopy(rec['otg'])
                if field=='summary':proof['summary']=proof['summary'].replace('DEPLOY_KEY','DIFFERENT_KEY')
                elif field=='input_sha256':proof['envelope']['input_sha256']='0'*64
                else:
                    step=otg['steps'][0]
                    if field=='source':step['src']='file:/tmp/forged-source'
                    else:step['dest']='file:/tmp/forged-target'
                    otg['tid']=og.tid_of(otg)
                with self.assertRaises(og.GrantError):og.verify(otg,proof,roster)

    async def test_real_cli_covering_reuses_original_proof_and_refuses_replay(self):
        original=self.f.sign_plan
        async def check_targets(plan):
            paths=[Path(s['dest'][5:]) for s in plan['steps'] if s.get('secret') and s.get('dest','').startswith('file:')]
            self.assertTrue(paths)
            self.assertTrue(all(not p.exists() for p in paths))
            result=await original(plan)
            self.assertTrue(all(not p.exists() for p in paths),'signing alone does not move the payload')
            self.assertIn(result['grant'],self.x._grants_out())
            return result
        self.f.sign_plan=check_targets
        await asyncio.wait_for(self.f.test_real_cli_ask_and_give_two_hosts_under_one_signature(),12)
        records=self.x._grants_out();self.assertEqual(len(records),1)
        record=next(iter(records.values()));self.assertEqual(record['used'],{'0':1,'1':1})
        path=Path(record['otg']['steps'][0]['dest'][5:]);before=path.read_bytes()
        result=await asyncio.wait_for(self.x.pull('B',record,0,{'mode':'file','path':str(path)}),3)
        self.assertEqual(result['result'],'refused');self.assertEqual(result['why'],'replay')
        self.assertEqual(path.read_bytes(),before)

    async def test_real_index_metadata_remains_refused_before_any_card(self):
        await self.f.test_grant_metadata_never_bypasses_secret_value_guard()


    def cache_compatibility(self,record,expect_reject=False):
        self.compatibility_cases.append(copy.deepcopy({'otg':record['otg'],'proof':record['proof'],
            'roster':[fixture.entry(self.f.phone.auth,self.f.phone.did)],
            'host_home':os.environ['HOME'],'expect_reject':expect_reject}))

    async def test_real_ids_json_cosmetics_and_home_protocol_boundary(self):
        local=os.urandom(16).hex();remote=wire.b64u(os.urandom(32))
        self.f.svc.keys=types.SimpleNamespace(id=local)
        self.f.svc._resolve=lambda ref:(remote,None)
        with self.f.svc.store._edit('owner-roster.json',{}) as rows:
            rows[remote]={'list':[fixture.entry(self.f.phone.auth,self.f.phone.did)]}
        for home in (False,True,'plain-why'):
            for version in (1,2):
                with self.subTest(home=home,version=version):
                    if home:
                        # Metadata only: no directory/file is created or read in
                        # this synthetic /home path. It exercises the existing
                        # POSIX own-home projector, unlike a /tmp HOME spelling.
                        patcher=mock.patch.dict(os.environ,{'HOME':'/home/aj-0300-'+Path(self.f.tmp.name).name})
                        patcher.start();self.addCleanup(patcher.stop)
                    root=Path.home() if home else Path(self.f.tmp.name)
                    if home:
                        projector=action_policy.display_normalizer(workdir=self.f.tmp.name)
                        self.assertEqual(projector('file:'+str(root/'display-only-source.key')),
                                         'file:'+str(root/'display-only-source.key'))
                        self.assertNotEqual(projector(str(root/'display-only-source.key')),
                                            str(root/'display-only-source.key'))
                    destination=root/('display-only-'+str(version)+'.key')
                    source=root/'display-only-source.key'
                    plan={'title':'Deployment {"a":1}','why':'{"reason":"release","n":1}','steps':[
                        {'secret':'DEPLOY_KEY','from':remote,'to':'self','src':'file:'+str(source),
                         'dest':'file:'+str(destination)}]}
                    if home=='plain-why':plan['why']='Inspect '+str(root/'display-only-source.key')
                    task,ask,card,_=await self.open('en',version,plan,metadata_only=bool(home))
                    self.assertEqual(card['summary'],og.card_text(ask.tool_input['otg']))
                    self.assertIn(str(source),card['summary']);self.assertIn(str(destination),card['summary'])
                    self.assertIn(local,card['owner_grant']['hosts']);self.assertIn(remote,card['owner_grant']['hosts'])
                    before=set(self.x._grants_out());raw_before=set(self.f.svc.store._read('grants-out.json',{}))
                    await self.approve(ask,card);result=await asyncio.wait_for(task,2)
                    if home=='plain-why' and version==2:
                        self.assertEqual(result['result'],'refused');self.assertEqual(result['why'],'bad_proof')
                        self.assertEqual(set(self.x._grants_out()),before)
                        self.assertEqual(set(self.f.svc.store._read('grants-out.json',{})),raw_before)
                        from agentj.list_approval import proof
                        _,did,sk,sig=ask.fut.result()
                        rejected={'otg':ask.tool_input['otg'],'proof':og.proof_of(ask.tool_input['otg'],proof(self.h,ask,did,sk,sig))}
                        self.cache_compatibility(rejected,expect_reject=True)
                    else:
                        self.assertEqual(result['result'],'signed')
                        record=self.x._grants_out()[result['grant']]
                        self.assertTrue(self.x.local_live(record));self.cache_compatibility(record)
                    self.assertFalse(destination.exists());self.assertFalse(source.exists())

    async def test_sensitive_typed_leaves_and_untyped_payloads_fail_closed(self):
        known='PRIVATE_FIXTURE_MATERIAL'
        project=Path(self.f.tmp.name)
        env=project/'.env';env.write_text('FIXTURE_KEY='+known+'\n');env.chmod(0o600)
        base=fixture.plan()
        cases=[]
        for field in ('title','why'):
            candidate=copy.deepcopy(base);candidate[field]=known;cases.append(candidate)
        candidate=copy.deepcopy(base);candidate['hosts']['unused']=known;cases.append(candidate)
        candidate=copy.deepcopy(base);candidate['hosts'][known]='Unused';cases.append(candidate)
        for field in ('name','src','dest'):
            candidate=copy.deepcopy(base);candidate['steps'][0][field]=known if field=='name' else 'file:/tmp/'+known
            cases.append(candidate)
        for text in ('Authorization: Bearer private-short-X',json.dumps({'password':'z'}),
                     base64.b64encode(json.dumps({'token':'short-private-X'}).encode()).decode()):
            candidate=copy.deepcopy(base);candidate['why']=text;cases.append(candidate)
        for candidate in cases:
            with self.subTest(field_values=candidate['why']):
                candidate['tid']=og.tid_of(candidate);text=og.card_text(candidate)
                inp={'grant':candidate['tid'],'card':text,'summary':text,'otg':candidate}
                result=await self.h.ask(og.TOOL,inp)
                self.assertEqual(result['behavior'],'deny');self.assertEqual(result['reason'],'invalid_display')
                self.assertFalse(self.h.asks)
        incomplete=project/'.env-incomplete';incomplete.mkdir()
        try:
            candidate=copy.deepcopy(base);text=og.card_text(candidate)
            result=await self.h.ask(og.TOOL,{'grant':candidate['tid'],'card':text,'summary':text,'otg':candidate})
            self.assertEqual(result['reason'],'invalid_display');self.assertFalse(self.h.asks)
        finally:incomplete.rmdir()
        normalizer=action_policy.display_normalizer(workdir=str(project))
        raw={'summary':'password=unregistered-private-X'}
        self.assertIs(og.typed_plan_display(raw,normalizer),normalizer)
        self.assertNotIn('unregistered-private-X',json.dumps(normalizer(raw)))
        # Exact business-shaped JSON from a native tool still uses the ordinary
        # full projector, never the Host controller's typed template.
        ordinary=fixture.plan();text=og.card_text(ordinary)
        inp={'grant':ordinary['tid'],'card':text,'summary':text,'otg':ordinary}
        self.sent.clear();task=asyncio.create_task(self.h.ask(og.TOOL,inp,_harness_permission=True));self.tasks.append(task)
        card=await outbound_card(self,self.h,self.sent,task)
        self.assertNotEqual(card['summary'],text)
        await self.h._app(self.f.s,self.f.phone.answer(card,False))
        self.assertEqual((await task)['behavior'],'deny')

    def test_sensitivity_api_is_per_call_and_keeps_ordinary_projection(self):
        normalizer=action_policy.display_normalizer(workdir=self.f.tmp.name)
        safe='{"purpose":"deployment","n":1}'
        unsafe='Authorization: Bearer short-private-X'
        self.assertNotEqual(normalizer(safe),safe)
        self.assertEqual(normalizer.sensitive_kinds(safe),frozenset())
        self.assertTrue(normalizer.sensitive_kinds(unsafe))
        work=[safe,unsafe]*20
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(normalizer.sensitive_kinds,work))
        self.assertTrue(all(bool(result)==(value==unsafe) for value,result in zip(work,results)))
        self.assertEqual(normalizer.sensitive_kinds(safe),frozenset())
        self.assertTrue(all(isinstance(name,str) for result in results for name in result))


if __name__=='__main__':unittest.main()
