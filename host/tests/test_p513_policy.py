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
from test_l1 import _owner_host, Phone, _ready


class PaidLibrary(unittest.IsolatedAsyncioTestCase):
    async def test_each_charge_needs_final_uv_and_destination_stays_bound(self):
        for mode in ('no_passkey', 'missing', 'no_uv', 'changed', 'valid'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                st=State(pathlib.Path(d));st.init()
                cfg={'api':'http://127.0.0.1:9','host_id':'fixture-host','tenant':{'slug':'fixture','name':'Fixture'},'linked_at':1,'last_seq':0}
                cloud.write_cloud(st,cfg)
                sent=[];h=_owner_host(st,sent);ph=Phone(st,with_passkey=mode!='no_passkey');s=_ready(h,ph);s.p33=True
                posts=[]
                def post(url, env, **kw):
                    posts.append((url,env));return 200, {'ok':True}
                fields={'op':'search','text':'fixture query','source':'fixture-library','top_k':2}
                await h.elevate.start()
                try:
                    task=asyncio.create_task(asyncio.to_thread(cloud.data_call,st,fields,post=post))
                    try:
                        for _ in range(200):
                            if h.asks or task.done():break
                            await asyncio.sleep(.005)
                        self.assertTrue(h.asks)
                        card=h._ask_msg(next(iter(h.asks.values())),s)
                        self.assertTrue(card['passkey_required'])
                        self.assertIn('credits_max',card['summary']);self.assertIn('fixture query',card['summary'])
                        answer=ph.answer(card,True);answer.pop('fa',None)
                        if mode not in ('no_passkey','missing'):
                            a=h.asks[card['id']]
                            ch=passkey.elevate_challenge(h.channel,ph.did,a.rid,a.pk_kind,a.rid,a.digest)
                            answer['fa']=ph.auth.assert_(ch,b'0',nonce=b'0',flags=1 if mode=='no_uv' else 5)
                        if mode=='changed':cloud.write_cloud(st,{**cfg,'host_id':'other-host'})
                        await h._answer(s,answer)
                        if mode in ('no_passkey','missing','no_uv'):
                            self.assertFalse(task.done());self.assertFalse(posts)
                            await h._answer(s,ph.answer(card,False))
                        if mode=='valid':
                            self.assertEqual((await asyncio.wait_for(task,3))[0],200)
                            await h._answer(s,answer)
                            self.assertEqual(len(posts),1)
                        else:
                            with self.assertRaises(cloud.CloudError):await asyncio.wait_for(task,3)
                            self.assertFalse(posts)
                    finally:
                        if not task.done():task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                finally:await h.elevate.stop()

    async def test_script_runs_frozen_bytes_only_after_actual_uv(self):
        for approve in (False, True):
            with self.subTest(approve=approve), tempfile.TemporaryDirectory() as d:
                root=pathlib.Path(d);st=State(root/'state');st.init()
                h=_owner_host(st,[]);ph=Phone(st,with_passkey=True);session=_ready(h,ph);session.p33=True
                engine=root/'pkg';(engine/'scripts').mkdir(parents=True)
                script=engine/report.ENGINE;marker=root/'effect'
                script.write_text('import pathlib,sys\npathlib.Path('+repr(str(marker))+').write_text(pathlib.Path(sys.argv[2]).read_text())\n')
                data=root/'data.json';data.write_text(json.dumps({'body':'approved final body'+'x'*2100+'TAIL-FINAL-ARGUMENT'}))
                state_env=patch.dict('os.environ', {'AGENTJ_STATE_DIR':str(st.root)})
                state_env.start()
                await h.elevate.start()
                try:
                    task=asyncio.create_task(asyncio.to_thread(report._run,engine,['publish',str(data)],True))
                    try:
                        for _ in range(200):
                            if h.asks or task.done():break
                            await asyncio.sleep(.005)
                        self.assertTrue(h.asks)
                        card=h._ask_msg(next(iter(h.asks.values())),session)
                        self.assertIn('TAIL-FINAL-ARGUMENT',card['summary']);self.assertTrue(card['passkey_required'])
                        obj=ph.answer(card,True);obj.pop('fa',None)
                        await h._answer(session,obj)
                        self.assertFalse(task.done());self.assertFalse(marker.exists())
                        a=h.asks[card['id']]
                        ch=passkey.elevate_challenge(h.channel,ph.did,a.rid,a.pk_kind,a.rid,a.digest)
                        await h._answer(session,{**obj,'fa':ph.auth.assert_(ch,b'0',nonce=b'0',flags=1)})
                        self.assertFalse(task.done());self.assertFalse(marker.exists())
                        script.write_text('raise SystemExit("mutable original executed")')
                        data.write_text('{"body":"changed after card"}')
                        await h._answer(session,ph.answer(card,approve))
                        result=await asyncio.wait_for(task,3)
                        if approve:
                            self.assertEqual(result.returncode,0,result.stderr)
                            self.assertIn('approved final body',marker.read_text())
                        else:
                            self.assertEqual(result.returncode,1);self.assertFalse(marker.exists())
                    finally:
                        if not task.done():task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                finally:
                    await h.elevate.stop()
                    state_env.stop()


    async def test_real_report_entry_freezes_workspace_template_before_signed_uv(self):
        # Use the unmodified plaza parser -> find_template -> wr_custom.load ->
        # schema/render chain, not an argv-recording script or a call to _run.
        for mutate in (False, True):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as d:
                root = pathlib.Path(d)
                engine, workspace, template, data, output = _real_report(root)
                st = State(root / 'state'); st.init()
                h = _owner_host(st, []); ph = Phone(st, with_passkey=True)
                session = _ready(h, ph); session.p33 = True
                parser = argparse.ArgumentParser()
                report.add_parser(parser.add_subparsers())
                args = parser.parse_args(['report', 'render', str(data), '--template', 'daily', '--out', str(output)])
                with patch.dict(os.environ, {'AGENTJ_REPORT_ENGINE': str(engine), 'AGENT_WORKSPACE': str(workspace),
                                             'AGENTJ_STATE_DIR': str(st.root)}):
                    await h.elevate.start()
                    task = asyncio.create_task(asyncio.to_thread(report.cmd, args))
                    try:
                        for _ in range(400):
                            if h.asks or task.done(): break
                            await asyncio.sleep(.005)
                        self.assertTrue(h.asks)
                        card = h._ask_msg(next(iter(h.asks.values())), session)
                        self.assertTrue(card['passkey_required'])
                        self.assertIn('APPROVED-TEMPLATE', card['summary'])
                        self.assertIn(str(template / 'page.html'), card['summary'])
                        self.assertFalse(output.exists())
                        answer = ph.answer(card, True)  # sign the frozen card first
                        if mutate:
                            (template / 'page.html').write_text('<h1>CHANGED-TEMPLATE {{ title }}</h1>')
                        await h._answer(session, answer)
                        code = await asyncio.wait_for(task, 5)
                        self.assertEqual(code, 2 if mutate else 0)
                        if mutate:
                            self.assertFalse(output.exists(), 'changed template must not render')
                        else:
                            self.assertIn('APPROVED-TEMPLATE', output.read_text())
                            self.assertIn('Approved data', output.read_text())
                    finally:
                        if not task.done(): task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        await h.elevate.stop()

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
