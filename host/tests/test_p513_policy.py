"""P513 final-executor regressions. Real broker + paired Ed25519 + real UV;
only the outbound HTTP transport is replaced. Script tests execute real local bytes.
"""
import _hermetic
import asyncio
import argparse
import contextlib
import io
import hashlib
import os
import shutil
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from agentj import cloud, passkey, report, action_policy
from agentj.state import State
from test_l1 import _ready
from _p541_device import _owner_host, Phone
from types import SimpleNamespace
from agentj import codex_procs, provenance, names
import sys
import signal


class PaidLibrary(unittest.IsolatedAsyncioTestCase):
    async def native_call(self, st, host, body, boundary=None, env=None, *, ingress='valid'):
        """Actual kernel child → elevate socket → application admission.

        Only the external HTTP sink is supplied inside the child. The observer
        can mutate fixtures at the real frozen-input/confirmation boundary; it
        always calls the original Host.ask, never supplies an approval result.
        """
        host.ask_ttl = 2  # an unexpected ordinary card fails promptly, never awaits a human
        path = names.elevate_sock(st)
        await host.elevate.start()  # actual private socket directory, mode and frame limit
        server = host.elevate.server
        anchor = "import subprocess,sys;sys.stdin.readline();sys.exit(subprocess.call([sys.executable,'-c',sys.argv[1]]))"
        child_env = {**os.environ, 'AGENTJ_STATE_DIR':str(st.root),
                     'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1]), **(env or {})}
        proc = await asyncio.create_subprocess_exec(sys.executable,'-c',anchor,body,env=child_env,
                    stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
        old_agent, ask = host.agent, host.ask
        host.agent = SimpleNamespace(kind='claude',session={'sessionId':'paid-library-fixture','pid':proc.pid,
                    'procStart':codex_procs.start_token(proc.pid)},native_source=provenance.system(),
                    research=False,phone_turn=False,cfg={'kind':'claude','dir':str(st.root.parent)})
        if ingress == 'invalid_start':
            # Keep the actual kernel peer/ancestor; its declared session start
            # no longer matches /proc, so it cannot be current invocation proof.
            host.agent.session['procStart'] = 'not-the-live-kernel-start'
        elif ingress == 'revoked_root':
            # Revoke the actual Host-held launch root, not a JSON/source label.
            host.__dict__.get('_approval_origins', {}).pop(host.desktop_turn, None)
            # #0302 (chairman 2026-11-09): a missing root alone no longer refuses the owner's own session process
            # (owner_local). It stays refused when the process was born while a non-owner turn was open.
            host._nonowner_floor, host._nonowner_spans = float('-inf'), [[float('-inf'), None]]   # child born in a guest turn
        else:
            self.assertEqual(ingress, 'valid')
        seen=[]
        async def observe(tool, inp, **kwargs):
            record=[tool,inp,None]; seen.append(record)
            if boundary: boundary(tool,inp)
            answer=await ask(tool,inp,**kwargs)
            record[2]=answer
            return answer
        host.ask = observe
        try:
            await asyncio.sleep(.03)
            proc.stdin.write(b'go\n'); await proc.stdin.drain()
            out,err = await asyncio.wait_for(proc.communicate(),10)
            self.assertEqual(proc.returncode,0,err.decode())
            self.assertFalse(host.asks)
            return json.loads(out.decode().splitlines()[-1]), seen
        finally:
            host.agent,host.ask = old_agent,ask
            if proc.returncode is None:
                # Only this fixture's own fresh process group, including its
                # waiting client, may be killed on a bounded failure.
                with contextlib.suppress(ProcessLookupError):
                    if os.getpgid(proc.pid) == proc.pid: os.killpg(proc.pid,signal.SIGKILL)
                await proc.wait()
            await host.elevate.stop(); await server.wait_closed()

    async def test_each_charge_needs_final_uv_and_destination_stays_bound(self):
        # Preserve every old UV-variant input; ordinary paid queries follow the
        # actual native entry. Passkey absence is not a new payment authority.
        for mode in ('no_passkey', 'missing', 'no_uv', 'changed', 'valid'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d); st=State(root/'state'); st.init()
                cfg={'api':'http://127.0.0.1:9','host_id':'fixture-host','tenant':{'slug':'fixture','name':'Fixture'},'linked_at':1,'last_seq':0}
                cloud.write_cloud(st,cfg)
                h=_owner_host(st,[]); ph=Phone(st,with_passkey=mode!='no_passkey'); _ready(h,ph)
                fields={'op':'search','text':'fixture query','source':'fixture-library','top_k':2}
                posts=root/'posts.json'
                body = """import json,pathlib
from agentj import cloud
from agentj.state import State
posts=[]
def post(url,env,**kw):
 posts.append([url,env]);return 200,{'ok':True}
try:
 result=cloud.data_call(State(),FIELDS,post=post)
 answer={'status':result[0]}
except cloud.CloudError as e:
 answer={'error':str(e)}
pathlib.Path(POSTS).write_text(json.dumps(posts))
print(json.dumps(answer))
""".replace('FIELDS',repr(fields)).replace('POSTS',repr(str(posts)))
                def boundary(tool, inp):
                    self.assertEqual(tool,'agentj data')
                    self.assertEqual(inp['request'],fields)
                    from agentj import library
                    self.assertEqual(inp['credits_max'],library.PRICES[fields['op']])
                    self.assertEqual(inp['currency'],'Agent J credits')
                    if mode=='changed': cloud.write_cloud(st,{**cfg,'host_id':'other-host'})
                result,seen=await self.native_call(st,h,body,boundary)
                self.assertEqual(len(seen),1)
                actual=json.loads(posts.read_text())
                if mode=='changed':
                    self.assertIn('changed',result['error']); self.assertEqual(actual,[])
                else:
                    self.assertEqual(result,{'status':200}); self.assertEqual(len(actual),1)
                    self.assertEqual(actual[0][0],cfg['api']+'/v1/host/data')

    async def test_paid_frame_cannot_raise_price_or_retarget_scope(self):
        for change in ('credits_max','boolean_cost','currency','destination','operation'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d);st=State(root/'state');st.init()
                cfg={'api':'http://127.0.0.1:9','host_id':'fixture-host','tenant':{'slug':'fixture','name':'Fixture'},'linked_at':1,'last_seq':0}
                cloud.write_cloud(st,cfg)
                h=_owner_host(st,[]);ph=Phone(st);_ready(h,ph)
                from agentj import library
                proposal={'request':{'op':'search','text':'fixture query','source':'fixture-library','top_k':2},
                    'destination':{'url':cloud.api_url(st,cfg)+'/v1/host/data','channel':cloud.channel_of(st),
                                   'host_id':cfg['host_id'],'tenant':cfg['tenant']},
                    'credits_max':library.PRICES['search'],'currency':'Agent J credits'}
                if change=='credits_max': proposal['credits_max']+=1
                elif change=='boolean_cost': proposal['credits_max']=True
                elif change=='currency': proposal['currency']='USD'
                elif change=='destination': proposal['destination']['host_id']='other-host'
                else: proposal['request']['op']='unregistered-paid-action'
                marker=root/'forbidden-charge'
                body="""import json,pathlib
from agentj import elevate,names
from agentj.state import State
st=State();r=elevate.client_request(st,{'t':'policy_cli','action':'payment','params':PROPOSAL},timeout=3,path=names.elevate_sock(st))
if r.get('result')=='approved': pathlib.Path(MARKER).write_text('effect')
print(json.dumps(r))
""".replace('PROPOSAL',repr(proposal)).replace('MARKER',repr(str(marker)))
                result,seen=await self.native_call(st,h,body)
                self.assertNotEqual(result.get('result'),'approved')
                self.assertEqual(seen,[])
                self.assertFalse(marker.exists())

    async def test_actual_invalid_or_revoked_native_ingress_has_zero_paid_and_script_effect(self):
        for consumer in ('paid','script'):
            for ingress in ('invalid_start','revoked_root'):
                with self.subTest(consumer=consumer,ingress=ingress), tempfile.TemporaryDirectory() as d:
                    root=pathlib.Path(d);st=State(root/'state');st.init()
                    h=_owner_host(st,[]);ph=Phone(st,with_passkey=True);_ready(h,ph)
                    effect=root/'forbidden-effect'
                    if consumer=='paid':
                        cfg={'api':'http://127.0.0.1:9','host_id':'fixture-host','tenant':{'slug':'fixture','name':'Fixture'},'linked_at':1,'last_seq':0}
                        cloud.write_cloud(st,cfg)
                        fields={'op':'search','text':'fixture query','source':'fixture-library','top_k':2}
                        body="""import json,pathlib
from agentj import cloud
from agentj.state import State
def post(url,env,**kw):
 pathlib.Path(EFFECT).write_text('HTTP effect');return 200,{'ok':True}
try:
 result=cloud.data_call(State(),FIELDS,post=post)
 answer={'status':result[0]}
except cloud.CloudError as e:
 answer={'error':str(e)}
print(json.dumps(answer))
""".replace('FIELDS',repr(fields)).replace('EFFECT',repr(str(effect)))
                    else:
                        engine=root/'pkg';(engine/'scripts').mkdir(parents=True)
                        (engine/report.ENGINE).write_text('import pathlib\npathlib.Path('+repr(str(effect))+').write_text("script effect")\n')
                        data=root/'data.json';data.write_text('{"body":"original frozen data"}')
                        body="""import json,pathlib
from agentj import report
r=report._run(pathlib.Path(ENGINE),['publish',DATA],True)
print(json.dumps({'returncode':r.returncode,'stderr':r.stderr}))
""".replace('ENGINE',repr(str(engine))).replace('DATA',repr(str(data)))
                    result,seen=await self.native_call(st,h,body,ingress=ingress)
                    self.assertEqual(len(seen),1,'must reach the actual final Host application admission')
                    self.assertEqual(seen[0][0],'agentj data' if consumer=='paid' else 'agentj report script')
                    self.assertEqual(seen[0][2]['behavior'],'deny')
                    self.assertEqual(seen[0][2]['reason'],'application_ingress_unverified')
                    if consumer=='paid':
                        self.assertEqual(result,{'error':'confirmation_required'})
                    else:
                        self.assertEqual(result['returncode'],1)
                        receipt=json.loads(result['stderr'])
                        # This is a structured authorization refusal, never an
                        # arbitrary exception/crash counted as a negative pass.
                        self.assertEqual(receipt['code'],'authorization_source_refused')
                        self.assertEqual(receipt['reason'],'application_ingress_unverified')
                        self.assertIs(receipt['approval_card_shown'],False)
                    self.assertFalse(effect.exists())
                    self.assertFalse(h.asks)

    async def test_script_runs_frozen_bytes_only_after_actual_uv(self):
        for approve in (False, True):
            with self.subTest(approve=approve), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d);st=State(root/'state');st.init()
                h=_owner_host(st,[]);ph=Phone(st,with_passkey=True);_ready(h,ph)
                engine=root/'pkg';(engine/'scripts').mkdir(parents=True)
                script=engine/report.ENGINE;marker=root/'effect'
                script.write_text('import pathlib,sys\npathlib.Path('+repr(str(marker))+').write_text(pathlib.Path(sys.argv[2]).read_text())\n')
                data=root/'data.json';data.write_text(json.dumps({'body':'approved final body'+'x'*2100+'TAIL-FINAL-ARGUMENT'}))
                body="""import json,pathlib
from agentj import report
r=report._run(pathlib.Path(ENGINE),['publish',DATA],True)
print(json.dumps({'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}))
""".replace('ENGINE',repr(str(engine))).replace('DATA',repr(str(data)))
                def boundary(tool, inp):
                    self.assertEqual(tool,'agentj report script')
                    self.assertIn('TAIL-FINAL-ARGUMENT',json.dumps(inp))
                    self.assertFalse(marker.exists())
                    script.write_text('raise SystemExit("mutable original executed")')
                    data.write_text('{"body":"changed after card"}')
                    # Preserve the denied/executed vector without resurrecting an
                    # extra ordinary card: the real emergency stop denies work.
                    if not approve: h.estop['on']=True
                result,seen=await self.native_call(st,h,body,boundary)
                self.assertEqual(len(seen),1)
                if approve:
                    self.assertEqual(result['returncode'],0,result['stderr'])
                    self.assertIn('approved final body',marker.read_text())
                else:
                    self.assertEqual(result['returncode'],1); self.assertFalse(marker.exists())
                    self.assertIn('execution_stopped',result['stderr'])

    async def test_real_report_entry_freezes_workspace_template_before_signed_uv(self):
        for mutate in (False, True):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d)
                engine,workspace,template,data,output=_real_report(root)
                st=State(root/'state');st.init()
                h=_owner_host(st,[]);ph=Phone(st,with_passkey=True);_ready(h,ph)
                body="""import argparse,json
from agentj import report
parser=argparse.ArgumentParser(); report.add_parser(parser.add_subparsers())
a=parser.parse_args(['report','render',DATA,'--template','daily','--out',OUTPUT])
print(json.dumps({'code':report.cmd(a)}))
""".replace('DATA',repr(str(data))).replace('OUTPUT',repr(str(output)))
                def boundary(tool, inp):
                    self.assertEqual(tool,'agentj report script')
                    self.assertIn('APPROVED-TEMPLATE',json.dumps(inp))
                    self.assertIn(str(template/'page.html'),json.dumps(inp))
                    self.assertFalse(output.exists())
                    if mutate: (template/'page.html').write_text('<h1>CHANGED-TEMPLATE {{ title }}</h1>')
                result,seen=await self.native_call(st,h,body,boundary,
                    {'AGENTJ_REPORT_ENGINE':str(engine),'AGENT_WORKSPACE':str(workspace)})
                self.assertEqual(len(seen),1)
                self.assertEqual(result['code'],2 if mutate else 0)
                if mutate: self.assertFalse(output.exists(),'changed template must not render')
                else:
                    self.assertIn('APPROVED-TEMPLATE',output.read_text())
                    self.assertIn('Approved data',output.read_text())

    async def test_real_report_entry_missing_data_cannot_appear_during_approval(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            engine, workspace, template, data, output = _real_report(root)
            data.unlink()
            st = State(root / 'state'); st.init()
            h = _owner_host(st, []); ph = Phone(st, with_passkey=True)
            _ready(h, ph)
            parser = argparse.ArgumentParser()
            report.add_parser(parser.add_subparsers())
            args = parser.parse_args(['report', 'render', str(data), '--template', 'daily', '--out', str(output)])
            with patch.dict(os.environ, {'AGENTJ_REPORT_ENGINE': str(engine), 'AGENT_WORKSPACE': str(workspace),
                                         'AGENTJ_STATE_DIR': str(st.root)}):
                await h.elevate.start()
                try:
                    errors = io.StringIO()
                    with contextlib.redirect_stderr(errors):
                        code = await asyncio.wait_for(asyncio.to_thread(report.cmd, args), 5)
                    self.assertEqual(code, 2)
                    self.assertIn('report data file', errors.getvalue())
                    self.assertFalse(h.asks, 'missing data must be refused before any approval card')
                    data.write_text('{"title":"new unapproved data"}')
                    await asyncio.sleep(.02)
                    self.assertFalse(output.exists())
                    self.assertFalse(h.asks)
                finally:
                    await h.elevate.stop()


class ScriptSnapshot(unittest.TestCase):
    def test_mutating_original_script_and_data_during_uv_cannot_change_executed_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);engine=root/'pkg';(engine/'scripts').mkdir(parents=True)
            script=engine/report.ENGINE
            script.write_text('import pathlib,sys\nprint(pathlib.Path(sys.argv[2]).read_text())\n')
            data=root/'data.json';data.write_text('{"destination":"approved.example","amount":5}')
            def approve(action, fields, **kw):
                self.assertEqual(action,'broker.execute')
                self.assertIn('approved.example',json.dumps(fields))
                script.write_text('raise SystemExit("changed script executed")')
                data.write_text('{"destination":"evil.example","amount":500}')
                return True
            with patch('agentj.action_policy.confirm_cli',side_effect=approve):
                result=report._run(engine,['publish',str(data)],capture=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('approved.example',result.stdout);self.assertNotIn('evil.example',result.stdout)

    def test_denial_and_symlink_have_no_script_effect(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);engine=root/'pkg';(engine/'scripts').mkdir(parents=True)
            marker=root/'effect'
            (engine/report.ENGINE).write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()')
            with patch('agentj.action_policy.confirm_cli',return_value=False):
                self.assertEqual(report._run(engine,['templates'],capture=True).returncode,1)
            self.assertFalse(marker.exists())
            (engine/'linked').symlink_to(marker)
            with self.assertRaises(ValueError):report._run(engine,['templates'],capture=True)
            self.assertFalse(marker.exists())


def _real_report(root):
    fixture = pathlib.Path(__file__).parent / 'fixtures/p513/web-page-report'
    engine = root / 'engine'
    receipt = json.loads((fixture / 'SOURCE.json').read_text())
    for name, digest in receipt['sha256'].items():
        assert hashlib.sha256((fixture / name).read_bytes()).hexdigest() == digest, name
    shutil.copytree(fixture, engine)
    workspace = root / 'workspace'
    template = workspace / 'report-templates/daily'
    template.mkdir(parents=True)
    (template / 'template.json').write_text(json.dumps({'schema': 'web-page-report.template/v1', 'id': 'daily'}))
    (template / 'schema.json').write_text(json.dumps({'type': 'object', 'required': ['title'],
                                                     'properties': {'title': {'type': 'string'}}}))
    (template / 'page.html').write_text('<h1>APPROVED-TEMPLATE {{ title }}</h1>')
    data = root / 'data.json'
    data.write_text('{"title":"Approved data"}')
    return engine, workspace, template, data, root / 'output.html'
