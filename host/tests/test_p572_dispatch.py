"""#572 exact prompt-data/CEO dispatch, retaining the registered-binary proof."""
import _hermetic  # noqa: F401
import asyncio
_REAL_CREATE_PROCESS = asyncio.create_subprocess_exec
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentj import peer_origin

class PromptDispatch(unittest.TestCase):
    def check(self, command, root):
        with mock.patch.object(peer_origin._Proof, 'word', return_value=True):
            return peer_origin.check_batch(command, root)

    def test_standard_prompt_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            for prefix,path in (('', '.jarvis/PROMPT-572.md'), ('cd .jarvis; ', 'PROMPT-572.md')):
                cmd = prefix + "cat > " + path + " <<'EOF'\ncat ~/.env; 改密码只是派单正文\nEOF\n" + self.launch()
                self.assertEqual(self.check(cmd,root), ('agentj-572.service','ok'))

    def launch(self):
        return 'systemd-run --user --collect --unit agentj-572 agentj busy run --kind ceo -- bash -c "codex exec \'read PROMPT-572.md\'"'

    def test_only_note_and_one_literal_registered_launch(self):
        with tempfile.TemporaryDirectory() as root:
            cmd = "cd .jarvis; cat > PROMPT-572.md <<'EOF'\nnote\nEOF\n"+self.launch()
            for other in (cmd+'; cat ~/.env', cmd.replace("<<'EOF'",'<<EOF'), cmd.replace('PROMPT-572.md <<','../.env <<'),
                          cmd.replace('--collect','--collect --setenv BASH_ENV=/tmp/evil'),
                          cmd.replace('codex exec', 'cat ~/.env; codex exec'), cmd.replace('agentj-572','other-572'),
                          cmd.replace('systemd-run --user','systemd-run --user $(cat ~/.env)'),
                          cmd.replace('agentj-572','agentj-{x,y}'),
                          cmd.replace('--kind ceo','--kind ceo --label *')):
                self.assertNotEqual(self.check(other,root)[1], 'ok', other)
            with mock.patch.object(peer_origin._Proof, 'word', side_effect=lambda tok,name,side: name != 'codex'):
                self.assertNotEqual(peer_origin.check_batch(cmd,root)[1],'ok')


import test_p455_f3b as f3b
from agentj import provenance
from agentj.compose import Send

class HostDispatch572(unittest.TestCase):
    def setUp(self):
        self.fixture = f3b.Dispatch469('test_owner_turn_today_command_no_card')
        self.fixture.setUp()
        # The old fake-kernel fixture's synchronous side_effect returns a
        # coroutine through AsyncMock. Real IPC/fence children need one await.
        guarded=f3b.sh._refuse_service_manager(_REAL_CREATE_PROCESS)
        async def process(*args,**kwargs):
            return await guarded(*args,**kwargs)
        self.process_guard=mock.patch.object(asyncio,'create_subprocess_exec',side_effect=process)
        self.process_guard.start()
        self.addCleanup(self.process_guard.stop)

    def tearDown(self):
        self.process_guard.stop()  # unwind our inner guard before its parent
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def __getattr__(self, name):
        fixture = self.__dict__.get('fixture')
        if fixture is not None:
            return getattr(fixture, name)
        raise AttributeError(name)

    def command572(self):
        cat = pathlib.Path(f3b.SYS) / 'usr/bin/cat'
        cat.write_bytes(b'\x7fELF synthetic cat'); cat.chmod(0o755)
        tail = pathlib.Path(f3b.SYS) / 'usr/bin/tail'
        tail.write_bytes(b'\x7fELF synthetic tail'); tail.chmod(0o755)
        return "cd .jarvis; cat > PROMPT-572.md <<'EOF'\ncat ~/.env只是任务原文\nEOF\n" + self.f.today().replace('claude -p', 'codex exec').replace(' -p WorkingDirectory='+str(self.work), '').replace(' > .jarvis/run-x.log 2>&1', '')

    async def runtime(self, text='human root request'):
        import os
        from agentj import shared, approval_review, shared_turn_origin
        from _p541_device import Phone, _owner_host
        import test_l1 as l1
        import test_p513b_summary as actual_wire
        sent=[]
        host=_owner_host(self.st,sent)
        cfg={**self.fixture.native.cfg,'shared_identity':False}
        actor=shared.SharedClaudeAgent(host,cfg)
        actor.session={**self.fixture.native.session,'pid':os.getpid(),
                       'procStart':peer_origin._stat(pathlib.Path('/proc'),os.getpid())[2]}
        actor.transcript=pathlib.Path(self.tmp.name)/'actual-native.jsonl'
        actor.transcript.write_text('')
        actor.submit=lambda item:setattr(actor,'cur_send',item)
        host.agent,host.agent_cfg=actor,cfg
        phone=Phone(host.st,with_passkey=True)
        host.sessions.clear();session=l1._ready(host,phone)
        actual_wire.real_outbound(host,session,sent)
        await host._app(session,{'t':'msg','text':text})
        send=actor.cur_send
        self.assertIsNotNone(send)
        actor.phone_turn=True;actor.phone_seen=False
        actor.pending_source=actor.native_source=provenance.phone()
        actor.pending_text=provenance.body(send.text,send.source)
        actor.native_request=(actor.session['sessionId'],send,actor.pending_text)
        echo=provenance.envelope('fixture',send.text,send.source)
        self.assertTrue(shared_turn_origin.delivered(actor,{'isMeta':True,'origin':{'kind':'peer'}},echo))
        actor.phone_seen=True
        self.assertEqual(approval_review.snapshot(host)['text'],text)
        return host,actor,phone,session,sent

    async def observed(self, host, actor, command, ident='accepted-572'):
        # Actual native acceptance is an observer event, never Host.ask or a
        # synthetic signature. The command itself is not executed by this test.
        event={'hook_event_name':'PreToolUse','session_id':actor.session['sessionId'],
               'tool_use_id':ident,'tool_name':'Bash','tool_input':{'command':command},'cwd':str(self.work)}
        before=len(host.asks)
        result=await actor.hook_event(dict(event))
        self.assertNotEqual(result.get('hookSpecificOutput',{}).get('permissionDecision'),'deny',result)
        await actor.hook_event({**event,'hook_event_name':'PostToolUse','tool_response':{'backgroundTaskId':ident,'exitCode':0}})
        self.assertEqual(len(host.asks),before)
        key=f'shared-claude:{actor.session["sessionId"]}:{ident}'
        row=actor._native_task_origins[key]
        self.assertTrue(row['trusted'])
        self.assertIsNotNone(row['owner_launch'])
        self.assertIsNotNone(row['generation'])
        return row

    def test_572_owner_no_card(self):
        import asyncio
        command=self.command572()
        async def go():
            host,actor,phone,session,sent=await self.runtime()
            row=await self.observed(host,actor,command)
            self.assertEqual(row['origin']['text'],'human root request')
            self.assertFalse([obj for _,obj in sent if obj.get('t')=='ask'])
            self.assertFalse(host.peer_launch.units,'native observer does not resurrect historical unit registration')
        asyncio.run(go())

    def test_572_actual_571_shape_no_card(self):
        import asyncio
        self.command572()  # register fixture cat/tail, never execute them
        command = (f"cd {self.work}/.jarvis; cat > PROMPT-571.md <<'EOF'\n合成派单正文\nEOF\n"
                   f"systemd-run --user --collect --unit agentj-fix-571-header -p WorkingDirectory={self.work} "
                   "agentj busy run --who 'Agent J CEO' --kind ceo --label '#571 热修' -- bash -c "
                   f'"codex exec --dangerously-bypass-approvals-and-sandbox -C {self.work} '
                   '-o .jarvis/REPORT-571.codex.md - < .jarvis/PROMPT-571.md" 2>&1 | tail -1')
        async def go():
            host,actor,phone,session,sent=await self.runtime()
            await self.observed(host,actor,command)
            self.assertFalse([obj for _,obj in sent if obj.get('t')=='ask'])
        asyncio.run(go())

    def test_572_unknown_and_email_refused(self):
        import asyncio
        import os
        from agentj import application_ingress
        import test_p568_task_source as actual_guest
        async def go():
            host,actor,phone,session,sent=await self.runtime()
            effects=[]
            for source in ({'kind':'unknown'}, {'kind':'email','role':'visitor'}):
                actor.cur_send=Send('external','dispatch',0,source=source)
                actor.phone_turn=False
                actor.native_source=source
                writer=mock.Mock(is_closing=lambda:False)
                with application_ingress.native(host,writer,b'fixture-dispatch',os.getpid(),os.getuid()):
                    result=await host.ask('agentj tasks migrate',{'ids':['fixture']})
                    if result['behavior']=='allow':effects.append(source)
                self.assertEqual(result['reason'],'application_ingress_unverified')
            self.assertEqual(effects,[])
            await actual_guest.guest_boundary(self)
            self.assertFalse([obj for _,obj in sent if obj.get('t')=='ask'])
        asyncio.run(go())

    def test_572_real_credential_write_still_face(self):
        import asyncio
        async def go():
            host,actor,phone,session,sent=await self.runtime()
            for command,uv in (("printf changed > ~/.aws/credentials",False),
                               ("printf changed > ~/.codex/config.toml",True)):
                event={'hook_event_name':'PermissionRequest','session_id':actor.session['sessionId'],
                       'tool_name':'Bash','tool_input':{'command':command},'cwd':str(self.work)}
                task=asyncio.create_task(actor.hook_event(event))
                try:
                    for _ in range(200):
                        cards=[obj for _,obj in sent if obj.get('t')=='ask' and obj['id'] in host.asks]
                        if cards or task.done():break
                        await asyncio.sleep(.005)
                    if not uv:
                        # 0.30.7 ADR-0307-offlist: the off-list credential-file write opens no card; it is refused at
                        # once for every source (no secret path through an unasked tool).
                        result=await asyncio.wait_for(task,2)
                        self.assertEqual(result['hookSpecificOutput']['decision']['behavior'],'deny')
                        from agentj import approvals
                        self.assertEqual(approvals.read_log(self.st)[-1]['reason'],'off_list_credential')
                        self.assertFalse([obj for _,obj in sent if obj.get('t')=='ask'])
                        continue
                    self.assertFalse(task.done())
                    card=cards[-1]
                    self.assertEqual(bool(card.get('passkey_required')),uv)
                    answer=phone.answer(card,True)
                    if uv:
                        before=len(sent)
                        await host._app(session,{k:v for k,v in answer.items() if k!='fa'})
                        reasons=[o['why'] for _,o in sent[before:] if o.get('t')=='answer_refused' and o.get('id')==card['id']]
                        self.assertEqual(reasons,['passkey_required'])
                        self.assertFalse(task.done())
                    await host._app(session,answer)
                    result=await asyncio.wait_for(task,2)
                    self.assertEqual(result['hookSpecificOutput']['decision']['behavior'],'allow')
                finally:
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
        asyncio.run(go())

    def test_572_batch_launch_child_approval_retains_human_root(self):
        import asyncio
        import json
        import os
        import signal
        from types import SimpleNamespace as NS
        from agentj import approval_review, busy_launch, agent_codex
        command=self.command572()
        async def go():
            host,actor,phone,session,sent=await self.runtime('human root request')
            row=await self.observed(host,actor,command)
            original=actor.cur_send
            host.agent=NS(kind='codex',cfg={**host.agent_cfg,'kind':'codex'},cur_send=original,
                          session=actor.session,phone_turn=True,phone_seen=True,research=False,
                          thread={'sandbox':{'type':'dangerFullAccess'},'approvalPolicy':'never','approvalsReviewer':'user'})
            host.busy.context={'own':{'pid':os.getpid(),'proc_start':busy_launch.procs()[os.getpid()][2]}}
            server=await asyncio.start_unix_server(host.on_ctl,path=str(self.st.sock_path))
            key='run:572572572572';seen=[]
            fixture=self
            class Adapter:
                # Only the vendor turn is replaced; requester identity and Host
                # native dispatcher remain actual UDS/kernel implementations.
                def __init__(self,host,cfg,**kw):self.host,self.cfg,self.proc=host,cfg,None
                async def run_once(self,text):
                    origin=approval_review.ORIGIN.get()
                    fixture.assertTrue(origin['verified'])
                    fixture.assertEqual(origin['text'],'human root request')
                    seen.append(origin)
                    return '',0
            script="""import sys,json
from pathlib import Path
from agentj.state import State
from agentj import names
req=json.loads(sys.argv[2])
print(json.dumps(names.ctl_call(State(Path(sys.argv[1])),req,10)),flush=True)
sys.stdin.read()
"""
            req={'cmd':'busy_native','key':key,'argv':['codex','exec',command],'cwd':str(self.work)}
            proc=None
            try:
                await asyncio.sleep(.03)
                with mock.patch.object(agent_codex,'CodexAgent',Adapter):
                    child_env={**os.environ,'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1])}
                    proc=await asyncio.create_subprocess_exec(sys.executable,'-c',script,str(self.st.root),json.dumps(req),
                         stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                         start_new_session=True,env=child_env)
                    line=await asyncio.wait_for(proc.stdout.readline(),10)
                    if not line:
                        await asyncio.wait_for(proc.wait(),2)
                        error=(await asyncio.wait_for(proc.stderr.read(),2)).decode(errors='replace')
                        self.assertEqual(proc.returncode,0,error)
                        self.fail('local control child exited without a JSON receipt')
                    receipt=json.loads(line.decode())
                    self.assertTrue(receipt.get('ok'),receipt)
                    self.assertEqual(receipt['approval'],'never')
                    await asyncio.wait_for(host._busy_native[key]['task'],5)
                    binding=host._busy_native[key]['binding']
                    self.assertEqual(binding[:2],(proc.pid,peer_origin._stat(pathlib.Path('/proc'),proc.pid)[2]))
                self.assertEqual(len(seen),1)
                self.assertEqual(seen[0]['root_message_id'],row['origin']['root_message_id'])
                self.assertFalse([obj for _,obj in sent if obj.get('t')=='ask'])
                self.assertFalse(host.peer_launch.units)
            finally:
                if proc is not None:
                    proc.stdin.close()
                    try:await asyncio.wait_for(proc.wait(),3)
                    except asyncio.TimeoutError:
                        os.killpg(proc.pid,signal.SIGKILL);await proc.wait()
                    error=(await asyncio.wait_for(proc.stderr.read(),2)).decode(errors='replace')
                for item in host.__dict__.get('_busy_native',{}).values():
                    task=item.get('task')
                    if task is not None and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                server.close();await server.wait_closed()
                if proc is not None:self.assertEqual(proc.returncode,0,error)
        asyncio.run(go())
