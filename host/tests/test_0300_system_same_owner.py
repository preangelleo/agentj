"""17:5x core: real systemd/busy, same-owner IPC and explicit credential reads."""
import _hermetic
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, Mock
from agentj import busy, busy_launch, codex_procs, fence, provenance, shared_hook, shared_risk, uv_list
from agentj.state import State
from _p541_device import _owner_host, Phone
import test_l1 as host_fixture


class CoreAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='core0300-');self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.work=self.home/'coding';self.work.mkdir()
        self.env={**os.environ,'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'config'),
                  'AGENTJ_STATE_DIR':str(self.home/'state'),'AGENTJ_TEST':'1',
                  'PYTHONPATH':str(Path(__file__).resolve().parents[1])}
        scope=patch.dict(os.environ,self.env);scope.start();self.addCleanup(scope.stop)
        self.st=State();self.st.init(relay='ws://127.0.0.1:9');self.st.set_agent_config('codex',str(self.work))
        self.st.perm_dir.mkdir(exist_ok=True)
        self.sent=[];self.h=_owner_host(self.st,self.sent);self.h.ask_ttl=.3
        self.phone=Phone(self.st,with_passkey=True);self.session=host_fixture._ready(self.h,self.phone)
        self.h.push_notify=Mock()
        self.sends=[]
        self.adapter=NS(host=self.h,kind='codex',cfg={'kind':'codex','dir':str(self.work),'fence':True},
            tid='system-fixture-native',proc=NS(pid=os.getpid(),returncode=None),
            session={'sessionId':'system-fixture-native','pid':os.getpid(),'procStart':codex_procs.start_token(os.getpid())},
            native_source=provenance.system(),cur_send=None,status='idle',halting=False,submit=self.sends.append,
            thread={'sandbox':{'type':'dangerFullAccess'},'approvalPolicy':'never','approvalsReviewer':'user'})
        self.h.agent=self.adapter;self.h.agent_cfg=self.adapter.cfg
        # A real system turn remains a system turn, never the fixture's earlier
        # desktop-owner receipt. Ordinary native execution needs no promotion.
        self.h.__dict__.get('_approval_origins',{}).clear();self.h.desktop_turn=None;self.h.cur_turn=None
        self.ask_calls=[];original=self.h.ask
        async def observe(tool,*a,**kw):
            self.ask_calls.append(tool)
            return await original(tool,*a,**kw)
        self.h.ask=observe
        self.channel=shared_hook.Channel(shared_risk.NativePathGuard(self.adapter),shared_hook.channel_path(self.st.perm_dir,'codex'))
        await self.channel.start();self.addAsyncCleanup(self.channel.stop)
        self.agentj=self.home/'agentj'
        self.agentj.write_text('#!'+sys.executable+'\nimport sys\nsys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]))+')\nfrom agentj.cli import main\nmain()\n')
        self.agentj.chmod(0o700)

    def evidence(self, name, value):
        dest=os.environ.get('CORE_0300_EVIDENCE')
        if dest:
            path=Path(dest);path.mkdir(parents=True,exist_ok=True)
            (path/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')

    async def guard(self, command, tool='Bash', inp=None):
        event={'hook_event_name':'PreToolUse','tool_name':tool,'tool_input':inp or {'command':command},
               'session_id':self.adapter.tid,'cwd':str(self.work)}
        code='import sys,json;from agentj import shared_hook;print(json.dumps(shared_hook._exchange(sys.argv[1],json.loads(sys.argv[2]),3)))'
        p=await asyncio.create_subprocess_exec(sys.executable,'-c',code,str(self.channel.path),json.dumps(event),
            env=self.env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(p.communicate(),5)
        self.assertEqual(p.returncode,0,err.decode())
        return json.loads(out)

    async def shell(self, command, *, env=None):
        decision=await self.guard(command)
        if decision.get('hookSpecificOutput',{}).get('permissionDecision')=='deny':
            return decision,None,None,None
        p=await asyncio.create_subprocess_exec('/bin/bash','-c',command,cwd=self.work,env=env or self.env,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out,err=await asyncio.wait_for(p.communicate(),30)
        return decision,p.returncode,out,err

    async def test_system_turn_actual_user_unit_busy_codex_exec_has_no_extra_card(self):
        self.assertEqual(sys.platform,'linux')
        # The full gate intentionally starts with a clean environment. Connect
        # to this UID's existing real user manager; never fake its transport.
        runtime=Path('/run/user')/str(os.getuid())
        self.assertTrue((runtime/'bus').is_socket(),'actual user session bus required')
        busenv={'XDG_RUNTIME_DIR':str(runtime),'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(runtime/'bus')}
        self.env.update(busenv)
        scope=patch.dict(os.environ,busenv);scope.start();self.addCleanup(scope.stop)
        check=await asyncio.to_thread(subprocess.run,['systemctl','--user','show-environment'],capture_output=True,timeout=5)
        self.assertEqual(check.returncode,0,'actual systemd user manager required')
        # Never print manager environment; it is not fixture data.
        unit='agentj-0300-core-'+secrets.token_hex(6)+'.service'
        marker=self.work/'provider-start.json';release=self.work/'provider-release';done=self.work/'provider-done.json'
        codex=self.work/'codex'
        codex.write_text('#!'+sys.executable+'\n'+
            'import sys,os,json,time\nfrom pathlib import Path\n'+
            'assert sys.argv[1:2]==["exec"]\n'+
            'row={"pid":os.getpid(),"start":Path("/proc/self/stat").read_text().rsplit(")",1)[1].split()[19],"unit":Path("/proc/self/cgroup").read_text()}\n'+
            'Path('+repr(str(marker))+').write_text(json.dumps(row))\n'+
            'end=time.monotonic()+15\nwhile not Path('+repr(str(release))+').exists() and time.monotonic()<end:time.sleep(.02)\n'+
            'assert Path('+repr(str(release))+').exists()\nPath('+repr(str(done))+').write_text(json.dumps({**row,"ended":True}))\n')
        codex.chmod(0o700)
        handlers=set()
        async def handle(reader,writer):
            task=asyncio.current_task();handlers.add(task)
            try:await self.h.on_ctl(reader,writer)
            finally:handlers.discard(task)
        ctl=await asyncio.start_unix_server(handle,path=str(self.st.sock_path))
        envargs=[key+'='+self.env[key] for key in ('HOME','XDG_CONFIG_HOME','AGENTJ_STATE_DIR','AGENTJ_TEST','PYTHONPATH')]
        argv=['systemd-run','--user','--wait','--pipe','--collect','--unit='+unit,
              '--property=RuntimeMaxSec=20','--working-directory='+str(self.work),'/usr/bin/env',*envargs,
              str(self.agentj),'busy','run','--who','core-fixture','--label','system/Webhook dispatch','--',str(codex),'exec','synthetic local task']
        self.assertEqual(await self.guard(shlex.join(argv)),{})
        p=await asyncio.create_subprocess_exec(*argv,env=self.env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            for _ in range(400):
                rows=busy.read_items(self.st.root)
                if marker.exists() and rows:
                    raw=json.loads((busy.items_dir(self.st.root)/(busy._safe(rows[0]['key'])+'.json')).read_text())
                    if raw.get('child_pid')==json.loads(marker.read_text())['pid']:break
                if p.returncode is not None:break
                await asyncio.sleep(.025)
            if not marker.exists() and p.returncode is not None:
                out,err=await asyncio.wait_for(p.communicate(),2)
                self.fail('real transient unit exited before vendor start: rc='+str(p.returncode)+'; '+err.decode(errors='replace')[:1000])
            self.assertTrue(marker.exists(),'fake vendor must actually start inside the real unit')
            started=json.loads(marker.read_text());row=rows[0]
            self.assertEqual(raw['child_pid'],started['pid'])
            self.assertEqual(raw['child_start'],started['start'])
            self.assertEqual(row['proc_start'],codex_procs.start_token(row['pid']))
            self.assertEqual(busy_launch.procs()[started['pid']][0],row['pid'])
            self.assertIn('agentj-work-'+str(started['pid'])+'.scope',started['unit'])
            props=await asyncio.to_thread(subprocess.run,['systemctl','--user','show',unit,'--property=MainPID','--property=ControlGroup'],capture_output=True,text=True,timeout=5)
            self.assertEqual(props.returncode,0)
            self.assertIn('MainPID='+str(row['pid']),props.stdout)
            self.assertIn(unit,props.stdout)
            release.write_text('finish')
            out,err=await asyncio.wait_for(p.communicate(),10)
            self.assertEqual(p.returncode,0,err.decode())
            self.assertTrue(json.loads(done.read_text())['ended'])
            finished=busy.finished_items(self.st.root)
            self.assertTrue(any(r.get('key')==row['key'] and r.get('exit_code')==0 for r in finished),finished)
            self.assertFalse(self.ask_calls);self.assertFalse(self.h.asks)
            self.assertFalse(any(o.get('t')=='ask' for _,o in self.sent))
            self.assertEqual(provenance.turn_source(self.adapter),provenance.system())
            self.evidence('actual-systemd-busy',{'argv':argv,'unit':unit,'systemctl':props.stdout,
                'writer_pid':row['pid'],'writer_start':row['proc_start'],'child':started,
                'completed':True,'exit_code':p.returncode,'extra_aj_cards':len(self.ask_calls)})
        finally:
            release.touch()
            await asyncio.to_thread(subprocess.run,['systemctl','--user','stop',unit],capture_output=True,timeout=5)
            if p.returncode is None:p.kill();await p.wait()
            ctl.close();await ctl.wait_closed()
            if handlers:await asyncio.gather(*list(handlers),return_exceptions=True)

    async def test_system_fixed_credential_count_is_denied_but_other_env_file_is_not(self):
        secret=self.work/'.env';secret.write_text('SYNTHETIC_FIXTURE=not-valued\nSECOND=line\n')
        ordinary=self.work/'ordinary.env';ordinary.write_text('ordinary-data\n')
        output=self.work/'count-output'
        decision,rc,_,_=await self.shell('wc -l < ~/coding/.env > '+shlex.quote(str(output)))
        self.assertIsNone(rc)
        self.assertEqual(decision['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertIn('credential_file_read_forbidden',decision['hookSpecificOutput']['permissionDecisionReason'])
        self.assertIn('agentj secret send',decision['hookSpecificOutput']['permissionDecisionReason'])
        self.assertFalse(output.exists());self.assertFalse(self.ask_calls)
        decision,rc,out,err=await self.shell('wc -l < '+shlex.quote(str(ordinary)))
        self.assertEqual((decision,rc,out.strip()),({},0,b'1'),err)
        # This guard is an explicit tool boundary, not a claim the main fence
        # universally seals arbitrary file-reading programs.
        argv=fence.wrap(self.st,['/bin/sh','-c','wc -l < "$HOME/coding/.env"'],str(self.work))
        raw=await asyncio.to_thread(subprocess.run,argv,env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual((raw.returncode,raw.stdout.strip()),(0,'2'))
        self.evidence('credential-boundary',{'synthetic_home':True,'guard_denied':True,
            'credential_effect_absent':not output.exists(),'ordinary_file_actual_read':True,
            'extra_aj_cards':len(self.ask_calls),'raw_namespace_direct_read_still_possible':True,
            'universal_os_seal_claimed':False})

    async def test_credential_inventory_aliases_cwd_and_read_only_metadata(self):
        secret=self.work/'.env';secret.write_text('SYNTHETIC=unvalued\n')
        alias=self.work/'innocent';alias.symlink_to(secret)
        hard=self.work/'hard';os.link(secret,hard)
        registered=self.work/'registered-secrets.txt';registered.write_text('SYNTHETIC=unvalued\n')
        self.h.preferences['api_keys']['extra_env_files']=[str(registered)]
        with patch('agentj.keys_found.sources',side_effect=AssertionError('guard must never read values')):
            for tool,inp in [('Read',{'file_path':str(alias)}),('Read',{'file_path':str(hard)}),
                    ('Bash',{'command':'cat -n "$HOME/coding/.env"'}),
                    ('Bash',{'command':'awk "END {print NR}" ~/coding/.env'}),
                    ('Bash',{'command':'wc -l .env','workdir':str(self.work)}),
                    ('Bash',{'command':'cd '+shlex.quote(str(self.work))+' && wc -l .env'}),
                    ('Bash',{'command':'wc -l '+shlex.quote(str(registered))})]:
                with self.subTest(tool=tool,inp=inp):
                    self.assertTrue(uv_list.credential_reads(tool,inp,host=self.h,cwd=str(self.home)))
                    decision=await self.guard('',tool,inp)
                    self.assertEqual(decision['hookSpecificOutput']['permissionDecision'],'deny')
        for command in ('printf "%s" ~/coding/.env','codex exec synthetic',
                        "cat '$HOME/coding/.env'",'cat ordinary.env'):
            with self.subTest(command=command):
                self.assertFalse(uv_list.credential_reads('Bash',{'command':command},host=self.h,cwd=str(self.work)))
        self.assertFalse(self.ask_calls)

    async def test_same_owner_turn_actual_friends_cli_has_no_extra_card_and_uv_stays_required(self):
        from agentj import peer_service, wire
        import test_p71_peer_service as peer_fixture
        import test_p405_same_owner as owner_fixture
        key_scope=patch.dict(os.environ,{peer_service.TEST_CERT_ENV:wire.b64u(os.urandom(32))})
        key_scope.start();self.addCleanup(key_scope.stop)
        other=State(self.home/'other-state');other.init(relay='ws://127.0.0.1:9')
        other.set_agent_config('codex',str(self.work))
        remote_host=_owner_host(other,[])
        remote_phone=Phone(other,with_passkey=True)
        remote_session=host_fixture._ready(remote_host,remote_phone)
        source=peer_service.PeerService(self.h,sessions=peer_fixture.FakeSessions(),net_factory=peer_fixture.FakeNet)
        remote=peer_service.PeerService(remote_host,sessions=peer_fixture.FakeSessions(),net_factory=peer_fixture.FakeNet)
        self.h.peers=source;remote_host.peers=remote
        await source.start();self.addAsyncCleanup(source.stop)
        await remote.start();self.addAsyncCleanup(remote.stop)
        self.assertIsNotNone(source.net);self.assertIsNotNone(remote.net)
        for service,keys in ((source,remote.keys),(remote,source.keys)):
            service.store.add_friend(keys.id,wire.b64u(keys.x_pub),wire.b64u(keys.ed_pub),keys.mbox)
        from agentj import owner_grant, passkey, controls
        for service,phone,session in ((source,self.phone,self.session),(remote,remote_phone,remote_session)):
            entry=service.xfer.binding_entry(phone.did)
            fa=phone.auth.assert_(owner_grant.bind_challenge(entry),b'',nonce=b'')
            sig=wire.b64u(phone.sk.sign(b'agentj-owner-roster-v1\n'+owner_grant.jcs({k:entry[k] for k in ('id','alg','spki','rp','dev')})))
            await service.xfer.bind_phone(session,{'t':'roster_bind','fa':fa,'roster_sig':sig})
            self.assertTrue(service.xfer.bound(phone.did))
        positions=[0,0]
        async def pump():
            for _ in range(40):
                await asyncio.sleep(.005)
                count=0
                for index,(src,dst,fid) in enumerate(((source,remote,source.keys.id),(remote,source,remote.keys.id))):
                    rows=src.net.sent[positions[index]:];positions[index]=len(src.net.sent)
                    for target,obj in rows:
                        self.assertEqual(target,dst.keys.id)
                        if obj.get('t') in ('same_owner','owner_status','owner_status_res'):
                            dst.on_app(fid,obj);count+=1
                        elif obj.get('t')=='pmsg':
                            dst._on_pmsg(fid,obj);count+=1
                if not count:return
            self.fail('local peer transport failed to settle')
        # Existing real Ed25519 consent/check machinery, never an active=True mock.
        choose=owner_fixture.SameOwnerTests.choose
        self.assertEqual((await choose(self,source,remote.keys.id,self.phone.did,self.phone.sk))[:2],(True,None))
        await pump()
        for service,fid,phone in ((source,remote.keys.id,self.phone),(remote,source.keys.id,remote_phone)):
            target={'friend':fid,'op':'owner-confirm-v3','value':service.same_owner.challenge(fid)}
            nonce=secrets.token_hex(16);stamp=int(time.time()*1000)
            digest=controls.object_digest('fr_set',target)
            proof={'channel':service.host.channel,'device':phone.did,'pub':wire.b64u(phone.sk.public_key().public_bytes_raw()),
                   'n':nonce,'ts':stamp,'sig':wire.b64u(phone.sk.sign(controls.signed_message(service.host.channel,phone.did,'fr_set',nonce,stamp,digest)))}
            proof['fa']=phone.auth.assert_(passkey.elevate_challenge(service.host.channel,phone.did,nonce,'fr_set',nonce,digest),b'',nonce=b'')
            self.assertIsNone(controls.check(service.st,service.host.nonces,service.host.channel,phone.did,proof,'fr_set',target))
            self.assertEqual((await service.write('fr_set',target,device=phone.did,proof=proof))[:2],(True,None))
            await pump()
        self.assertTrue(source.same_owner.active(remote.keys.id))
        self.assertTrue(remote.same_owner.active(source.keys.id))
        self.assertTrue((await remote.tell(source.keys.id,'Synthetic same-owner device task'))['ok'])
        await pump()
        for _ in range(100):
            if self.sends:break
            await asyncio.sleep(.01)
        self.assertTrue(self.sends)
        send=self.sends[-1];self.assertEqual(send.source['kind'],'same_owner')
        self.adapter.cur_send=send;self.h.cur_turn=send.turn
        relation=source.same_owner.challenge(remote.keys.id)
        target_state=remote.st.root
        before=len(source.net.of('pmsg'));self.sent.clear();self.ask_calls.clear()
        env={**self.env,peer_service.ENV:str(source.sock_path)}
        command=shlex.join([str(self.agentj),'friends','--json','tell',remote.keys.id,'Synthetic local follow-up'])
        decision,rc,out,err=await self.shell(command,env=env)
        self.assertEqual((decision,rc),({},0),err.decode() if err else '')
        result=json.loads(out)
        self.assertTrue(result['ok'],result);self.assertEqual(result['route'],'main')
        self.assertEqual(len(source.net.of('pmsg')),before+1)
        self.assertEqual(source.net.of('pmsg')[-1][0],remote.keys.id)
        self.assertEqual(source.same_owner.challenge(remote.keys.id),relation)
        self.assertEqual(remote.st.root,target_state)
        self.assertFalse(self.ask_calls);self.assertFalse(any(o.get('t')=='ask' for _,o in self.sent))
        self.assertEqual(provenance.turn_source(self.adapter)['kind'],'same_owner')
        # Same-owner does not waive a concrete listed control-file mutation.
        self.h.ask_ttl=2
        original=self.st.devices_path.read_bytes()
        task=asyncio.create_task(self.guard('', 'Write',{'file_path':str(self.st.devices_path),'content':'{}'}))
        for _ in range(200):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done())
        ask=next(iter(self.h.asks.values()));self.assertTrue(ask.require_passkey)
        answer=self.phone.answer(self.h._ask_msg(ask,self.session),True);answer.pop('fa')
        await self.h._answer(self.session,answer)
        self.assertFalse(ask.fut.done());self.assertEqual(self.st.devices_path.read_bytes(),original)
        await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(ask,self.session),False))
        refused=await task
        self.assertEqual(refused['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertEqual(self.st.devices_path.read_bytes(),original)
        self.evidence('actual-same-owner-cli',{'command':command,'local_peer':source.keys.id,
            'remote_peer':remote.keys.id,'route':result['route'],'actual_cli_exit':rc,
            'bilateral_real_signed_uv_ceremony':True,'transport':'local FakeNet only',
            'same_owner_extra_aj_cards':0,'listed_write_still_requires_fresh_uv':True,
            'signed_no_uv_answer_rejected':True,'listed_write_bytes_unchanged':True})

    async def test_claude_real_token_hook_and_unavailable_native_fallback_keep_credential_denial(self):
        from agentj.shared import SharedClaudeAgent
        secret=self.work/'.env';secret.write_text('SYNTHETIC_ONLY=unvalued\n')
        adapter=SharedClaudeAgent(self.h,{'kind':'claude','dir':str(self.work),'shared_identity':False})
        adapter.session={'sessionId':'claude-system-fixture','pid':os.getpid()}
        adapter.issue_hook_token()
        channel=shared_hook.Channel(adapter,shared_hook.channel_path(self.st.root,'claude'))
        await channel.start()
        event={'hook_event_name':'PreToolUse','tool_name':'Bash','tool_input':{'command':'wc -l < ~/coding/.env'},
               'session_id':adapter.session['sessionId'],'cwd':str(self.work)}
        argv=shlex.split(shared_hook.settings(channel.path,('PreToolUse',))['hooks']['PreToolUse'][0]['hooks'][0]['command'])
        old=self.h.agent;self.h.agent=adapter
        try:
            p=await asyncio.create_subprocess_exec(*argv,env=self.env,stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,err=await asyncio.wait_for(p.communicate(json.dumps(event).encode()),5)
            self.assertEqual(p.returncode,0,err.decode())
            response=json.loads(out) if out.strip() else {}
            self.assertEqual(response.get('hookSpecificOutput',{}).get('permissionDecision'),'deny')
            self.assertIn(uv_list.CREDENTIAL_READ_REASON,response['hookSpecificOutput']['permissionDecisionReason'])
            self.assertFalse(self.ask_calls)
        finally:
            self.h.agent=old
            await channel.stop()
        for family in ('claude','codex','opencode'):
            decision=shared_hook.fallback('PreToolUse',family,event,True)
            self.assertEqual(decision['permissionDecision'],'deny')
            self.assertIn(uv_list.CREDENTIAL_READ_REASON,decision['permissionDecisionReason'])

    async def test_actual_peer_no_tools_namespace_policy_is_not_widened(self):
        import test_p568_task_source as existing
        await existing.guest_boundary(self)
