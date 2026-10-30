"""P138: fail closed, no inherited host secrets, one-launch owner passkey authorization."""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agentj import agent, approvals, claude_statusline, elevate, elevate_helper, fence, passkey, wire
from agentj.bots import native
from test_l1 import _state, _host, _ready, Phone
from test_p57_passkey import Authenticator, pk_record


class Boundaries(unittest.TestCase):
    def test_secret_commands_refused_before_card_and_at_execution(self):
        with tempfile.TemporaryDirectory() as d:
            req = dict(name='TEST_KEY', purpose='test', dest='file:'+d+'/key', verify_cmd='touch '+d+'/escaped')
            with self.assertRaises(elevate.Refused) as e:
                elevate.norm_secret(req, Path(d)/'state')
            self.assertEqual(e.exception.why, 'verify_cmd_disabled')
            with patch.object(elevate.subprocess, 'run') as run:
                self.assertEqual(elevate.verify_secret(req, bytearray(b'fake')), {'verify':'fail','detail':'command_disabled'})
                run.assert_not_called()
            self.assertFalse(Path(d,'escaped').exists())
            req.pop('verify_cmd')
            self.assertEqual(elevate.norm_secret(req, Path(d)/'state')['verify'], '')
            req['verify_url']='https://example.invalid/check'
            self.assertIn('GET https://', elevate.norm_secret(req, Path(d)/'state')['verify'])

    def test_auxiliary_environment_secret_negative_safe_positive(self):
        base={'HOME':'/tmp/fake','PATH':'/usr/bin','LANG':'zh_CN.UTF-8','LC_ALL':'C','CODEX_HOME':'/tmp/native',
              'AGENTSRELAY_OPENAI_KEY':'fake','AGENTSRELAY_ROUTE':'fake','OTHER_TOKEN':'fake','OTHER_API_KEY':'fake',
              'CLAUDE_CODE_OAUTH_TOKEN':'fake','ANTHROPIC_API_KEY':'fake','OPENAI_API_KEY':'fake','AGENTJ_ELEVATE_SOCK':'fake'}
        for kind in ('claude','codex','opencode','gemini'):
            with self.subTest(kind=kind):
                got=fence.auxiliary_environment(base, kind)
                self.assertEqual(got['LANG'],base['LANG'])
                self.assertEqual(got['HOME'],base['HOME'])
                self.assertEqual(got['PATH'],base['PATH'])
                self.assertEqual(got['CODEX_HOME'],base['CODEX_HOME'])
                self.assertFalse(any(k.startswith('AGENTSRELAY_') or k.endswith(('_TOKEN','_API_KEY')) for k in got),got.keys())
        task = fence.auxiliary_environment({**base,'AGENTJ_PERM_TOKEN':'new-claim'},'claude',extra_keep=('AGENTJ_PERM_TOKEN',))
        self.assertNotIn('AGENTJ_PERM_TOKEN',task)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',task)

    def test_task_claim_file_is_private_consumed_and_refuses_symlink(self):
        from agentj import permtool
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'task-claim-test';path.write_text('one-time-fake');path.chmod(0o600)
            with patch.dict(os.environ,{'AGENTJ_PERM_CLAIM':str(path)}):
                self.assertEqual(permtool.claim_value(d+'/perm.sock'),'one-time-fake')
                self.assertFalse(path.exists())
                target=Path(d)/'target';target.write_text('fake');target.chmod(0o600);path.symlink_to(target)
                with self.assertRaises(OSError):permtool.claim_value(d+'/perm.sock')
                self.assertTrue(target.exists())
                path.unlink();path.write_text('fake');path.chmod(0o644)
                with self.assertRaises(ValueError):permtool.claim_value(d+'/perm.sock')
                self.assertTrue(path.exists())

    def test_native_bot_actual_child_environment_and_reaped(self):
        children=[]
        original=subprocess.Popen
        def spawn(*a,**kw):
            p=original(*a,**kw);children.append(p);return p
        def cmd(p, root, env, selected):
            return [sys.executable,'-c','import os,json; print(json.dumps({"result":json.dumps({"text":json.dumps(sorted(os.environ)),"tools":[]})}))']
        base={'AGENTSRELAY_KEY':'fake','OTHER_TOKEN':'fake','OTHER_API_KEY':'fake','CLAUDE_CODE_OAUTH_TOKEN':'fake'}
        with patch.dict(os.environ,base),patch.object(native,'executable',return_value=sys.executable),patch.object(native,'command',side_effect=cmd),patch.object(native.subprocess,'Popen',side_effect=spawn):
            r=native.invoke_native(SimpleNamespace(name='claude',model=None),[])
        names=json.loads(r.text)
        self.assertNotIn('AGENTSRELAY_KEY',names)
        self.assertNotIn('OTHER_TOKEN',names)
        self.assertNotIn('OTHER_API_KEY',names)
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN',names)
        self.assertIn('PATH',names)
        self.assertTrue(children)
        self.assertTrue(all(p.poll() is not None for p in children),'no test children remain')

    def test_interpreter_positive_direct_command_negative_and_symlink(self):
        for exe in ('sh','/bin/bash','zsh','python','python3','python3.13','python3.13t','pythonw','node','perl'):
            self.assertTrue(elevate.norm_sudo({'argv':[exe,'-c','pass'],'why':'run a harmless test command'})['interpreter'],exe)
        self.assertFalse(elevate.norm_sudo({'argv':['/usr/bin/true'],'why':'run a harmless test command'})['interpreter'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'innocent';p.symlink_to('/bin/bash')
            self.assertTrue(elevate.interpreter_command([str(p)]))
        namespace={'__name__':'helper_test'}
        exec(elevate_helper.HELPER_SOURCE,namespace)
        # root helper rejects a signed-input route before any process launch.
        fake={'argv':['python3','-c','pass'],'why':'run a harmless test command','channel':'c'}
        with patch.object(elevate_helper.os,'geteuid',return_value=0), patch('builtins.open',return_value=io.StringIO('{"channel":"c"}')),patch.object(sys,'stdin',io.StringIO(json.dumps(fake))),patch.object(sys,'stderr',io.StringIO()),patch.object(subprocess,'run') as run:
            with self.assertRaises(SystemExit) as e:namespace['main']()
            self.assertEqual(e.exception.code,126)
            self.assertIn('password_required',sys.stderr.getvalue())
            run.assert_not_called()

    def test_statusline_success_and_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'meta';p.write_text(json.dumps({'original':{'command':'printf safe'}}))
            with patch.object(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'{}'))),patch.object(claude_statusline.subprocess,'run',return_value=SimpleNamespace(returncode=7)) as run:
                self.assertEqual(claude_statusline.tap(p),7)
                self.assertEqual(run.call_args.kwargs['timeout'],5)
            with patch.object(sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'{}'))),patch.object(claude_statusline.subprocess,'run',side_effect=subprocess.TimeoutExpired('test',5)):
                self.assertEqual(claude_statusline.tap(p),1)


class SudoPhone(unittest.TestCase):
    def test_interpreter_requires_sealed_password_even_when_helper_installed(self):
        import test_f17 as f17
        fixture=f17.Flows();fixture.setUp()
        try:
            async def script(host,session,ph):
                card=await fixture.wait_card()
                self.assertTrue(card['interpreter'])
                self.assertFalse(card.get('helper'))
                msg=f17.answer(ph,card,True,f17.PW)
                unsigned_pw={k:v for k,v in msg.items() if k not in ('ct','epk')}
                # Correct approve-only signature cannot bypass the password route.
                import time
                unsigned_pw['sig']=wire.b64u(ph.sk.sign(elevate.signed_message(ph.channel,ph.did,card['id'],'sudo','allow',card['n'],msg['ts'],elevate.shown_digest('sudo',elevate.shown_fields(card)),None)))
                await host._app(session,unsigned_pw)
                self.assertFalse(fixture.done())
                await host._app(session,msg)
            with patch.object(elevate.Elevator,'helper_devices',return_value=['any-phone']),patch.object(elevate_helper,'run') as helper:
                result,host,ph=fixture.run_flow({'t':'sudo','argv':['sh','-c','printf safe'],'why':'run a harmless test command'},script)
            self.assertEqual((result['result'],result['stdout']),('done','safe'))
            helper.assert_not_called()
            self.assertFalse(host.elevate.cards)
            self.assertFalse((fixture.st.perm_dir/elevate.SOCK_NAME).exists(),'test serve socket closed')
        finally:fixture.tearDown()


class PhoneSandbox(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.envpatch=patch.dict(os.environ,{"XDG_CONFIG_HOME":self.tmp.name+"/config","HOME":self.tmp.name})
        self.envpatch.start()
        self.st=_state(self.tmp.name)
        self.work=Path(self.tmp.name)/'work';self.work.mkdir()
        self.st.set_agent_config('claude',str(self.work))
        self.sent=[];self.host=_host(self.st,self.sent)
        self.a=agent.ClaudeAgent(self.host,{**self.st.agent_config(),'session_mode':'independent'})
        self.host.agent=self.a
        self.a.native_source={"kind":"terminal","role":"owner"}
        self.phone=Phone(self.st);self.session=_ready(self.host,self.phone)
        self.auth=Authenticator();self.st.set_passkey(self.phone.pub,pk_record(self.auth))
        self.host.ask_ttl=1
        # No serve loop/relay/browser is started; the production signed answer path is called directly.
        self.host._post=Mock()

    async def asyncTearDown(self):
        pending=[t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
        for t in pending:t.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        self.assertFalse([t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()])
        self.envpatch.stop()
        self.tmp.cleanup()

    async def wait_ask(self):
        for _ in range(100):
            if self.host.asks:return next(iter(self.host.asks.values()))
            await asyncio.sleep(.001)
        self.fail('no sandbox card')

    def answer(self,a,ok=True,**kw):
        msg=self.host._ask_msg(a,self.session)
        obj=self.phone.answer(msg,ok)
        if ok:
            assertion=self.auth.assert_(wire.unb64u(msg['fa_ch']),b'0',nonce=b'0',**kw)
            obj['fa']={k:assertion[k] for k in ('id','cd','ad','sig')}
        return obj

    async def test_fail_closed_passkey_binds_launch_and_replay_and_no_persistence(self):
        before=self.st.config_path.read_bytes()
        with patch.object(fence,'problem',return_value='no_bwrap'):
            self.assertIsNone(self.a.launch_argv(['/usr/bin/true']))
            task=asyncio.create_task(self.a.authorized_launch_argv(['/usr/bin/true']))
            card=await self.wait_ask()
            self.assertIsNone(card.scope)
            self.assertTrue(card.require_passkey)
            self.assertIn('本次不隔离运行',card.summary)
            self.assertIn('bubblewrap',card.summary)
            msg=self.host._ask_msg(card,self.session)
            await self.host._answer(self.session,self.phone.answer(msg,True))
            self.assertFalse(card.fut.done(),'signature alone is insufficient')
            await self.host._answer(self.session,self.answer(card,flags=0x01))
            self.assertFalse(card.fut.done(),'UV off')
            valid=self.answer(card)
            await self.host._answer(self.session,valid)
            self.assertEqual(await task,['/usr/bin/true'])
            self.assertFalse(self.a.isolation_effective)
            self.assertEqual(self.st.config_path.read_bytes(),before)
            task=asyncio.create_task(self.a.authorized_launch_argv(['/usr/bin/true']))
            second=await self.wait_ask()
            await self.host._answer(self.session,valid)
            self.assertFalse(second.fut.done(),'previous launch cannot authorize next launch')
            replay=self.phone.answer(self.host._ask_msg(second,self.session),True);replay['fa']=valid['fa']
            await self.host._answer(self.session,replay)
            self.assertFalse(second.fut.done(),'passkey proof binds card id and shown content')
            await self.host._answer(self.session,self.answer(second,False))
            self.assertIsNone(await task)
            self.assertFalse(self.host.asks)

    async def test_task_actual_subprocess_environment_and_reaped(self):
        from agentj import tasks
        scheduler=tasks.Scheduler(self.host)
        self.host.agent_cfg['fence']=False
        self.st.perm_dir.mkdir(mode=0o700,exist_ok=True)
        server=await asyncio.start_unix_server(self.host.on_perm,path=str(self.st.perm_sock_path))
        children=[]
        original=asyncio.create_subprocess_exec
        async def spawn(*a,**kw):
            child=await original(*a,**kw);children.append(child);return child
        script='import os,json,sys; from agentj.permtool import Link; link=Link(); assert link.claim(); sys.stdin.readline(); print(json.dumps({"type":"result","result":json.dumps(sorted(os.environ))}))'
        try:
            with patch.dict(os.environ,{'AGENTSRELAY_OPENAI_KEY':'fake','OTHER_TOKEN':'fake','OTHER_API_KEY':'fake','CLAUDE_CODE_OAUTH_TOKEN':'fake'}),patch.object(tasks,'prompt_for',return_value='test'),patch.object(agent.ClaudeAgent,'argv',return_value=[sys.executable,'-c',script]),patch.object(agent,'hooks_blocked',return_value=None),patch.object(asyncio,'create_subprocess_exec',side_effect=spawn):
                out,_=await scheduler._exec('claude',{'dir':str(self.work)},False)
            names=json.loads(out)
            for name in ('AGENTSRELAY_OPENAI_KEY','OTHER_TOKEN','OTHER_API_KEY','CLAUDE_CODE_OAUTH_TOKEN'):
                self.assertNotIn(name,names)
            self.assertIn('PATH',names)
            self.assertNotIn('AGENTJ_PERM_TOKEN',names)
            self.assertIn('AGENTJ_PERM_CLAIM',names,'only a fresh local claim file path survives')
            self.assertFalse(any(k.startswith('AGENTSRELAY_') or k.endswith(('_TOKEN','_API_KEY')) for k in names))
        finally:
            await scheduler._kill()
            for child in children:await child.wait()
            server.close();await server.wait_closed()
            self.st.perm_sock_path.unlink(missing_ok=True)
        self.assertTrue(children)
        self.assertTrue(all(p.returncode is not None for p in children),'no scheduled child remains')
        self.assertFalse(list(self.st.perm_dir.glob('task-claim-*')),'unclaimed local claim files cleaned')

    async def test_no_passkey_cannot_allow_but_can_deny(self):
        other=Phone(self.st);s=_ready(self.host,other,cid=12)
        self.host.lang='en'
        task=asyncio.create_task(self.host.ask('SandboxOnce',{'reason':'no_bwrap'},batch=False,require_passkey=True))
        a=await self.wait_ask();msg=self.host._ask_msg(a,s)
        self.assertIn('Sandbox unavailable',msg['summary'])
        self.assertNotIn('沙箱',msg['summary'])
        self.assertTrue(msg['passkey_required']);self.assertNotIn('fa',msg)
        await self.host._answer(s,other.answer(msg,True))
        self.assertFalse(a.fut.done())
        await self.host._answer(s,other.answer(msg,False))
        self.assertEqual((await task)['behavior'],'deny')

    async def test_timeout_denies_and_available_fence_needs_no_card(self):
        self.host.ask_ttl=.01
        with patch.object(fence,'problem',return_value='no_bwrap'):
            self.assertIsNone(await self.a.authorized_launch_argv(['/usr/bin/true']))
        with patch.object(fence,'problem',return_value=None),patch.object(fence,'wrap',return_value=['fenced','true']):
            self.assertEqual(await self.a.authorized_launch_argv(['true']),['fenced','true'])
        self.assertFalse(self.host.asks)
        self.a.cfg['fence']=False
        with patch.object(fence,'problem') as probe:
            self.assertEqual(await self.a.authorized_launch_argv(['true']),['true']);probe.assert_not_called()


if __name__=='__main__':unittest.main()
