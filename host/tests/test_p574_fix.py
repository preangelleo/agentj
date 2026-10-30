import _hermetic  # noqa: F401
import asyncio
import os
import sys
import json
from types import SimpleNamespace
from agentj.compose import Send
from agentj import provenance
from test_p574_owner_grant import HostFlows


class Repairs(HostFlows):
    async def test_ordinary_task_continues_only_at_owner_admission(self):
        x = self.svc.xfer
        x.admit(self.send, 'Deploy the app')
        scope = x.task_scope()
        signed = await self.sign_plan({'title':'Deploy','why':'Deployment','steps':[
            {'secret':'DEPLOY_KEY','from':'B','to':'self','dest':'file:/tmp/p574-fix'}]})
        later = Send(self.phone.did, 'later', 18, source=provenance.phone())
        x.admit(later, '继续同一任务')
        self.h.agent.cur_send = later
        self.assertEqual(x.task_scope(), scope)
        self.assertIsNotNone(x.covering('DEPLOY_KEY','B','A','file:/tmp/p574-fix'))
        fresh = Send(self.phone.did, 'fresh', 19, source=provenance.phone())
        x.admit(fresh, 'Start a new task')
        self.h.agent.cur_send = fresh
        self.assertNotEqual(x.task_scope(), scope)
        self.assertIsNone(x.covering('DEPLOY_KEY','B','A','file:/tmp/p574-fix'))
        self.assertIn(signed['grant'], x._grants_out())

    async def test_real_uds_unrelated_same_uid_cannot_sign(self):
        await self.h.elevate.start()
        try:
            env={**os.environ,'AGENTJ_STATE_DIR':str(self.st.root),
                 'AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path),
                 'PYTHONPATH':str(__import__('pathlib').Path(__file__).resolve().parents[1])}
            proc=await asyncio.create_subprocess_exec(sys.executable,'-c',
                'import socket,json,os; s=socket.socket(socket.AF_UNIX); s.connect(os.environ["AGENTJ_ELEVATE_SOCK"]); '
                's.sendall((json.dumps({"t":"sx","op":"grant","pid":os.getpid(),"task":"fake","plan":{"title":"Probe","why":"Probe","steps":[{"host":"B","cat":"delete","what":"Clean","paths":["/tmp/p574-probe"],"max":1}]}})+"\\n").encode()); print(s.recv(65536).decode())',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
            try:
                out,err=await asyncio.wait_for(proc.communicate(),1)
                self.assertEqual(json.loads(out)['why'],'caller')
            finally:
                if proc.returncode is None: proc.kill();await proc.wait()
            self.assertFalse(self.h.asks)
        finally:
            await self.h.elevate.stop()

    async def test_otg_more_returns_to_original_remote_and_moves_key(self):
        from agentj import wire
        from agentj.elevate import remember
        from test_p574_owner_grant import entry
        peer = HostFlows()
        await peer.asyncSetUp()
        self.addCleanup(peer.tmp.cleanup)
        peer.svc.keys = SimpleNamespace(id='B')
        peer.svc._resolve = lambda ref: ('A', None)
        with peer.svc.store._edit('owner-roster.json', {}) as rows:
            rows['A'] = {'list':[entry(self.phone.auth, self.phone.did)]}
        frames = []
        def a_send(fid, obj):
            frames.append(('A', fid, obj))
            asyncio.get_running_loop().call_soon(peer.svc.xfer.on_app, 'A', obj)
            return True
        def b_send(fid, obj):
            frames.append(('B', fid, obj))
            asyncio.get_running_loop().call_soon(self.svc.xfer.on_app, 'B', obj)
            return True
        self.svc._send, peer.svc._send = a_send, b_send
        self.svc.xfer.admit(self.send, 'Deploy')
        original = await self.sign_plan({'title':'Deploy','why':'Deployment','steps':[
            {'host':'B','cat':'delete','what':'Clean','paths':['/tmp/p574-more'],'max':1}]})
        await asyncio.sleep(.03)
        ids = peer.svc.xfer.live_for('A', [original['grant']], 'delegated-task')
        peer.send.source = {'kind':'same_owner','role':'task','fid':'A','otg':ids}
        source = __import__('pathlib').Path(self.tmp.name) / 'source.key'
        source.write_bytes(b'isolated-test-value')
        remember(self.st, 'c'*32, {'kind':'secret','name':'NEW_KEY','result':'saved',
            'where':{'mode':'file','path':str(source)},'receipt':{}})
        dest = __import__('pathlib').Path(peer.tmp.name) / 'new.key'
        self.h.agent.cur_send = None  # Origin finished delegating; the durable task remains current.
        request = asyncio.create_task(peer.svc.xfer.cli({'op':'ask','peer':'A','name':'NEW_KEY',
            'why':'Deploy needs this key','dest':'file:'+str(dest)}, peer.tmp.name))
        for _ in range(200):
            if self.h.asks or request.done(): break
            await asyncio.sleep(.005)
        self.assertFalse(request.done(), request.result() if request.done() else '')
        self.assertTrue(self.h.asks)
        self.assertFalse(peer.h.asks, 'receiving host must not show an approval card')
        card = next(iter(self.h.asks.values()))
        self.assertEqual(card.only, self.phone.did)
        self.assertIn('NEW_KEY', card.summary)
        await self.h._answer(self.s, self.phone.answer(self.h._ask_msg(card,self.s), True))
        answer = await asyncio.wait_for(request, 3)
        self.assertEqual(answer['result'], 'saved', answer)
        self.assertEqual(dest.read_bytes(), b'isolated-test-value')
        self.assertTrue(any(obj.get('t') == 'otg_more' for _,_,obj in frames))
        self.assertEqual(len(self.svc.xfer._grants_out()), 2)
        self.assertEqual(self.svc.xfer._grants_out()[original['grant']]['used'], {})

    async def test_real_owned_claude_stream_then_delayed_uv(self):
        import pathlib
        from unittest.mock import patch
        from agentj.agent import ClaudeAgent
        root = pathlib.Path(self.tmp.name)
        binary = root / 'bin'
        binary.mkdir()
        cli = binary / 'agentj'
        cli.write_text('#!/bin/sh\nexec ' + sys.executable + ' -m agentj.cli "$@"\n')
        cli.chmod(0o700)
        plan = root / 'native-plan.json'
        plan.write_text(json.dumps({'title':'Native','why':'Native task','steps':[
            {'host':'B','cat':'delete','what':'Clean','paths':['/tmp/p574-native'],'max':1}]}))
        claude = binary / 'claude'
        claude.write_text('#!/bin/sh\nexec '+sys.executable+' '+str(pathlib.Path(__file__).with_name('fakeclaude.py'))+' "$@"\n')
        claude.chmod(0o700)
        cfg = {**self.h.agent_cfg, 'kind':'claude','dir':str(root),'fence':False,'session_mode':'independent',
               'approvals':'standard','high_risk_warnings':True}
        native = ClaudeAgent(self.h,cfg)
        self.h.agent = native
        self.h.agent_cfg = cfg
        send = Send(self.phone.did, 'native-owned', 40, text='RUN: agentj secret grant --file '+str(plan)+' --json', source=provenance.phone())
        self.svc.xfer.admit(send, send.text)
        env = {'AGENTJ_CLAUDE_BIN':str(claude),
               'FAKE_CLAUDE_TOOL_RESULTS':'1','AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path),
               'AGENTJ_STATE_DIR':str(self.st.root),'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1]),
               'PATH':str(binary)+os.pathsep+os.environ['PATH']}
        await self.h.elevate.start()
        server = await asyncio.start_unix_server(self.h.on_perm, str(self.st.perm_sock_path))
        runner = None
        try:
            with patch.dict(os.environ,env):
                runner = asyncio.create_task(native.run())
                native.submit(send)
                for _ in range(1000):
                    if self.h.asks: break
                    await asyncio.sleep(.01)
                self.assertTrue(self.h.asks, self.st.root.joinpath('host.log').read_text()[-2000:])
                await asyncio.sleep(.1)  # Native has already produced tool_result and its final answer.
                self.assertIs(native.cur_send, send)
                self.assertFalse(self.svc.xfer._grants_out())
                ask = next(iter(self.h.asks.values()))
                await self.h._answer(self.s,self.phone.answer(self.h._ask_msg(ask,self.s),True))
                for _ in range(300):
                    if self.svc.xfer._grants_out() and native.cur_send is None:break
                    await asyncio.sleep(.01)
                self.assertEqual(len(self.svc.xfer._grants_out()),1)
                self.assertIsNone(native.cur_send)
        finally:
            self.h.stopping.set()
            if runner:
                runner.cancel()
                await asyncio.gather(runner,return_exceptions=True)
            if native.proc:
                await native._kill(native.proc)
            await self.h.elevate.stop()
            server.close()
            await server.wait_closed()

    async def test_real_shared_hook_native_success_only(self):
        import pathlib
        from unittest.mock import patch
        from agentj import shared, shared_hook
        root = pathlib.Path(self.tmp.name)
        registry = root / 'claude-config' / 'sessions'
        registry.mkdir(parents=True)
        patcher=patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(registry.parent)})
        patcher.start();self.addCleanup(patcher.stop)
        import socket
        messaging=socket.socket(socket.AF_UNIX)
        messaging.bind(str(root / 'native.sock'))
        self.addCleanup(messaging.close)
        binary = root / 'shared-bin'; binary.mkdir()
        cli = binary / 'agentj'
        cli.write_text('#!/bin/sh\nexec '+sys.executable+' -m agentj.cli "$@"\n');cli.chmod(0o700)
        plan = root / 'shared-plan.json'
        plan.write_text(json.dumps({'title':'Shared native','why':'Shared task','steps':[
            {'host':'B','cat':'delete','what':'Clean','paths':['/tmp/p574-shared'],'max':1}]}))
        command = 'agentj secret grant --file '+str(plan)+' --json'
        transcript = root / 'shared.jsonl';transcript.touch()
        cfg = {**self.h.agent_cfg,'kind':'claude','dir':str(root),'session_mode':'shared','fence':False}
        native = shared.SharedClaudeAgent(self.h,cfg)
        self.h.agent,self.h.agent_cfg=native,cfg
        native.cur_send = self.send
        native.phone_turn = native.phone_seen = True
        native.pending_source = provenance.phone()
        native.loop = asyncio.get_running_loop()
        native.transcript = transcript
        hookpath = self.st.root / 'claude-hook.sock'
        channel = shared_hook.Channel(native,hookpath)
        await channel.start();await self.h.elevate.start()
        code = r'''
import json,sys,subprocess,pathlib
hook,path,transcript,command,wd=sys.argv[1:]
sys.stdin.readline()
block={'type':'tool_use','id':'shared-native-call','name':'Bash','input':{'command':command}}
pathlib.Path(transcript).write_text(json.dumps({'type':'assistant','message':{'id':'message','role':'assistant','content':[block]}})+'\n')
event={'session_id':'shared-session','cwd':wd,'tool_name':'Bash','tool_input':{'command':command},'tool_use_id':'shared-native-call'}
def emit(kind,**extra):
    p=subprocess.run([sys.executable,hook,path,kind],input=json.dumps(dict(event,hook_event_name=kind,**extra)),capture_output=True,text=True)
    return json.loads(p.stdout or '{}')
answer=emit('PermissionRequest')
print(json.dumps(answer),flush=True)
sys.stdin.readline()
if answer.get('hookSpecificOutput',{}).get('decision',{}).get('behavior')=='allow':
    p=subprocess.run(command,shell=True,capture_output=True,text=True,cwd=wd)
    print(p.stdout,flush=True)
    print(json.dumps(emit('PostToolUse',tool_response={'stdout':p.stdout,'stderr':p.stderr,'exitCode':p.returncode})),flush=True)
'''
        env={**os.environ,'PATH':str(binary)+os.pathsep+os.environ['PATH'],
             'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1]),
             'AGENTJ_ELEVATE_SOCK':str(self.h.elevate.sock_path),'AGENTJ_STATE_DIR':str(self.st.root)}
        proc=await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(pathlib.Path(shared_hook.__file__)),
            str(hookpath),str(transcript),command,str(root),stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
        try:
            native.session={'sessionId':'shared-session','pid':proc.pid,'cwd':str(root),
                'messagingSocketPath':str(root / 'native.sock'),'status':'working'}
            (registry / 'owner.json').write_text(json.dumps(native.session))
            native.issue_hook_token()
            self.svc.xfer.admit(self.send,'Shared deployment')
            self.svc.xfer.begin_execution(self.send)
            proc.stdin.write(b'go\n');await proc.stdin.drain()
            first=await asyncio.wait_for(proc.stdout.readline(),4)
            self.assertEqual(json.loads(first)['hookSpecificOutput']['decision']['behavior'],'allow',first)
            self.assertFalse(self.h.asks)
            self.assertFalse(self.svc.xfer._grants_out())
            # A background CLI can obtain metadata but cannot cause any transfer before native success.
            from agentj.elevate import client_request
            req={'t':'sx','op':'grant','cwd':str(root),'plan':json.loads(plan.read_text())}
            pending=await asyncio.to_thread(client_request,self.st,req)
            self.assertEqual(pending['result'],'pending')
            self.assertFalse(self.h.asks)
            self.assertFalse(self.svc.xfer._grants_out())
            proc.stdin.write(b'run\n');await proc.stdin.drain()
            for _ in range(400):
                if self.h.asks:break
                await asyncio.sleep(.01)
            self.assertTrue(self.h.asks)
            await asyncio.sleep(.05)
            ask=next(iter(self.h.asks.values()))
            await self.h._answer(self.s,self.phone.answer(self.h._ask_msg(ask,self.s),True))
            await asyncio.wait_for(self.svc.xfer.broker.drain(),3)
            out,err=await asyncio.wait_for(proc.communicate(),3)
            self.assertEqual(proc.returncode,0,err.decode())
            self.assertEqual(len(self.svc.xfer._grants_out()),1)
        finally:
            if proc.returncode is None:proc.kill();await proc.wait()
            await channel.stop();await self.h.elevate.stop()

    async def test_task_expiry_tracks_signature_not_initial_message(self):
        from unittest.mock import patch
        import time
        x = self.svc.xfer
        t0 = time.time()
        with patch('time.time',return_value=t0):
            x.admit(self.send,'Deployment')
        t1 = t0 + 300
        with patch('time.time',return_value=t1):
            signed=await self.sign_plan({'title':'Deploy','why':'Deployment','steps':[
                {'secret':'DEPLOY_KEY','from':'B','to':'self','dest':'file:/tmp/p574-time'}]})
        with patch('time.time',return_value=t0 + 86400 + 100):
            resumed=Send(self.phone.did,'late-continuation',90,source=provenance.phone())
            x.admit(resumed,'继续同一任务')
            self.h.agent.cur_send=resumed
            self.assertIsNotNone(x.covering('DEPLOY_KEY','B','A','file:/tmp/p574-time'))
            self.assertIn(signed['grant'],x._grants_out())

    async def test_real_old_friends_cli_cannot_attach_grant_native_tell_can(self):
        import pathlib
        from agentj import wire
        from agentj.peer_service import ENV
        self.svc.store.add_friend('B', wire.b64u(os.urandom(32)), wire.b64u(os.urandom(32)), 'test-mailbox')
        self.svc.net = SimpleNamespace()
        self.svc.seat_refusal = lambda: None
        frames=[]
        self.svc._send=lambda fid,obj:frames.append((fid,obj)) or True
        self.svc.xfer.admit(self.send,'Delegate deployment')
        self.svc.xfer.begin_execution(self.send)
        await self.svc._start_sock()
        env={**os.environ,ENV:str(self.svc.sock_path),'AGENTJ_STATE_DIR':str(self.st.root),
             'PYTHONPATH':str(pathlib.Path(__file__).resolve().parents[1])}
        old=await asyncio.create_subprocess_exec(sys.executable,'-c',
            'import sys,json,socket,os; req=json.loads(sys.stdin.readline()); s=socket.socket(socket.AF_UNIX); '
            's.connect(os.environ["AGENTJ_FRIENDS_SOCK"]);s.sendall((json.dumps(req)+"\\n").encode());print(s.recv(65536).decode())',
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env)
        try:
            signed=await self.sign_plan({'title':'Delegate','why':'Deployment','steps':[
                {'host':'B','cat':'delete','what':'Clean','paths':['/tmp/p574-delegate'],'max':1}]})
            tid=signed['grant']
            req={'t':'tell','friend':'B','text':'Clean the deployment directory','grant':tid,'cwd':self.tmp.name}
            old.stdin.write((json.dumps(req)+'\n').encode());await old.stdin.drain()
            out,err=await asyncio.wait_for(old.communicate(),3)
            self.assertEqual(json.loads(out)['why'],'caller',out)
            self.assertFalse(self.svc.xfer._grants_out()[tid].get('tasks'))
            self.assertFalse(any(obj.get('t')=='pmsg' for _,obj in frames))
            native={'command':'agentj friends tell B "Clean the deployment directory" --grant '+tid}
            self.svc.xfer.broker.register('native-tell','Bash',native,self.tmp.name)
            self.assertEqual((await self.svc.xfer.broker.execute('Bash',native,'native-tell'))['behavior'],'allow')
            proc=await asyncio.create_subprocess_exec(sys.executable,'-m','agentj.cli','friends','tell','B',
                'Clean the deployment directory','--grant',tid,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,cwd=self.tmp.name)
            out,err=await asyncio.wait_for(proc.communicate(),4)
            self.assertEqual(proc.returncode,0,err.decode())
            self.assertIn(b'SECRET_TASK_PENDING',out)
            self.assertFalse(any(obj.get('t')=='pmsg' for _,obj in frames))
            self.svc.xfer.broker.complete('native-tell',True,out.decode())
            await self.svc.xfer.broker.drain()
            messages=[obj for _,obj in frames if obj.get('t')=='pmsg']
            self.assertEqual(len(messages),1,messages)
            self.assertEqual(messages[0]['otg'],[tid])
            self.assertFalse(self.h.asks)
        finally:
            if old.returncode is None:old.kill();await old.wait()
            self.svc.server.close();await self.svc.server.wait_closed()
