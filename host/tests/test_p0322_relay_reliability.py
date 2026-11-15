"""0307: actual CLI/socket reconnect, four real child slots and no replay after an effect."""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

from agentj import busy_native, history, busy, background_done
import time
from agentj.serve import Host
from agentj.shared_hook import peer_pid
from test_p0322_relay_native import Journey as Base


class Reliability(unittest.IsolatedAsyncioTestCase):
    configure=Base.configure
    cli=Base.cli
    close_server=Base.close_server

    async def asyncSetUp(self):
        await Base.asyncSetUp(self)
        self.server.close();await self.server.wait_closed();self.st.sock_path.unlink(missing_ok=True)
        self.host.hist=history.History(self.st,on=True);self.host.hist_emit=Mock()
        self.host.hist_add=lambda *a,**kw:Host.hist_add(self.host,*a,**kw)
        self.host.preferences={};self.host.lang='zh'
        launch=self.host.busy.launch
        self.host.busy=busy.Board(self.st.root);self.host.busy.launch=launch
        self.host._open_question=lambda:False
        self.agent.status='idle';self.agent.is_down=lambda:False
        self.agent.q=asyncio.Queue();self.agent.submit=self.agent.q.put_nowait
        self.notifier=background_done.Notifier(self.host)
        self.processes={};self.auto_release=False;self.drop_receipt=False;self.raise_after_effect=False
        self.addAsyncCleanup(self.release_children)
        async def child(host,cfg,text,mode,it):
            key=it['key'];self.started.append((mode,cfg['fence'],text))
            script="from pathlib import Path;import sys;Path(sys.argv[1]).write_text('started');sys.stdin.buffer.read(1);Path(sys.argv[2]).write_text('one actual effect')"
            p=await asyncio.create_subprocess_exec(sys.executable,'-c',script,'started-'+key,'effect-'+key,
                cwd=self.work,stdin=asyncio.subprocess.PIPE)
            self.processes[key]=p
            if self.auto_release:p.stdin.write(b'x');await p.stdin.drain()
            await p.wait()
            busy.write_finished(host.st.root,it,None if self.raise_after_effect else p.returncode)
            busy.remove_item(host.st.root,it['key'])
            if self.raise_after_effect:raise RuntimeError('synthetic post-effect disconnect')
            return p.returncode
        busy_native.run.side_effect=child
        self.server=await asyncio.start_unix_server(self.serve,path=self.st.sock_path)

    async def serve(self,reader,writer):
        try:
            req=json.loads(await reader.readline());self.received.append(req)
            pid,uid=peer_pid(writer.get_extra_info('socket'))
            try:
                if req.get('cmd')=='agentsrelay_native':result=await self.relay.start(self.host,req,pid,uid)
                else:result=self.relay.query(self.host,req,pid,uid)
            except ValueError as exc:result={'ok':False,'error':str(exc)}
            if self.drop_receipt and req.get('cmd')=='agentsrelay_native':return
            writer.write((json.dumps(result)+'\n').encode());await writer.drain()
        finally:writer.close();await writer.wait_closed()

    async def release_children(self):
        self.auto_release=True
        for p in list(self.processes.values()):
            if p.returncode is None:
                try:p.stdin.write(b'x');await p.stdin.drain()
                except (BrokenPipeError,ConnectionResetError):pass
        for row in getattr(self.host,'_busy_native',{}).values():
            try:await asyncio.wait_for(row['task'],5)
            except Exception:pass

    async def until(self,predicate):
        for _ in range(500):
            if predicate():return
            await asyncio.sleep(.01)
        self.fail('fixture state did not settle')

    async def dispatch(self,text='ordinary task'):
        rc,out,err=await self.cli('codex',text)
        self.assertEqual(rc,0,out+err)
        return json.loads(out)['key']

    async def test_initial_missing_socket_then_live_host_retries_before_any_accept(self):
        self.server.close();await self.server.wait_closed();self.st.sock_path.unlink(missing_ok=True)
        p=await asyncio.create_subprocess_exec(sys.executable,'-m','agentj.cli','relay','codex','ordinary task',
            cwd=self.work,env=self.env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        line=await asyncio.wait_for(p.stderr.readline(),5)
        self.assertIn(b'not connected',line)
        self.server=await asyncio.start_unix_server(self.serve,path=self.st.sock_path)
        out,err=await asyncio.wait_for(p.communicate(),15)
        self.assertEqual(p.returncode,0,out.decode()+err.decode())
        key=json.loads(out)['key'];await self.until(lambda:(self.work/('started-'+key)).exists())
        self.assertEqual(len(self.received),1)
        self.auto_release=True;await self.release_children()
        self.assertEqual(len(list(self.work.glob('effect-*'))),1)

    async def test_receipt_lost_status_reattaches_to_one_effect_and_never_resumes_execution(self):
        self.drop_receipt=True
        rc,out,err=await self.cli('codex','ordinary task')
        self.assertNotEqual(rc,0);answer=json.loads(out)
        self.assertEqual(answer['error'],'outcome_unknown');key=answer['key']
        await self.until(lambda:(self.work/('started-'+key)).exists())
        self.drop_receipt=False
        rc,out,_=await self.cli('status',key)
        self.assertEqual(rc,0);self.assertEqual(json.loads(out)['status'],'running')
        self.auto_release=True;await self.release_children()
        rc,out,_=await self.cli('status',key)
        self.assertEqual(json.loads(out)['status'],'exit_zero')
        self.assertFalse(json.loads(out)['replay_allowed'])
        self.assertEqual(len([r for r in self.received if r.get('cmd')=='agentsrelay_native']),1)
        self.assertEqual(len(self.started),1);self.assertEqual(len(list(self.work.glob('effect-*'))),1)

    async def test_four_actual_children_fifth_waits_then_uses_released_slot(self):
        keys=[await self.dispatch() for _ in range(5)]
        await self.until(lambda:len(list(self.work.glob('started-*')))==4)
        self.assertEqual(sum(p.returncode is None for p in self.processes.values()),4)
        self.assertFalse((self.work/('started-'+keys[4])).exists())
        rc,out,_=await self.cli('status',keys[4]);self.assertEqual(json.loads(out)['status'],'queued')
        self.processes[keys[0]].stdin.write(b'x');await self.processes[keys[0]].stdin.drain()
        await self.until(lambda:(self.work/('started-'+keys[4])).exists())
        self.assertEqual(sum(p.returncode is None for p in self.processes.values()),4)
        self.auto_release=True;await self.release_children()
        self.assertEqual(len(list(self.work.glob('effect-*'))),5)
        rc,out,_=await self.cli('stats');stats=json.loads(out)
        self.assertEqual(stats['counts']['exit_zero'],5)
        self.assertEqual(stats['scope'],'current_host_process');self.assertIsNone(stats['cost_usd'])
        self.assertIsNone(stats['fallback_rate']);self.assertIsNone(stats['quota_reset_at'])

    async def test_post_effect_failure_notifies_real_main_queue_once_without_replay(self):
        self.raise_after_effect=True;self.auto_release=True
        key=await self.dispatch('ordinary task')
        await self.host._busy_native[key]['task']
        self.assertEqual(len(list(self.work.glob('effect-*'))),1)
        for receipt in busy.finished_items(self.st.root):
            self.host.busy.finished.append({**receipt,'foreign':False})
        self.notifier.tick()
        send=self.agent.q.get_nowait()
        self.assertEqual(send.source['kind'],'system');self.assertIsNone(getattr(send,'owner_chain',None))
        self.assertIn('不自动重放',send.text)
        self.assertNotIn('ordinary task',send.text)
        self.assertNotIn('synthetic post-effect disconnect',send.text)
        self.assertEqual(len(self.host.hist.turns),1)
        send.state='delivered';self.notifier.complete(send)
        second=await self.dispatch('another ordinary task');await self.host._busy_native[second]['task']
        for receipt in busy.finished_items(self.st.root):
            self.host.busy.finished.append({**receipt,'foreign':False})
        self.notifier.tick(time.time()+61)
        self.assertTrue(self.agent.q.empty(),'same fixed failure category is deduplicated for ten minutes')
        rc,out,_=await self.cli('status',key)
        self.assertEqual(json.loads(out)['status'],'unknown')
        self.assertTrue(self.notifier.pending,'dedupe delays the second job, never drops its notification')
        self.notifier.tick(time.time()+601)
        self.assertFalse(self.agent.q.empty())
        self.assertEqual(len(self.started),2,'query never submits another native turn')
        self.assertEqual(len(list(self.work.glob('effect-*'))),2)

    async def test_no_host_exhausts_only_pre_send_connections_and_unknown_status_never_executes(self):
        self.server.close();await self.server.wait_closed();self.st.sock_path.unlink(missing_ok=True)
        rc,out,err=await self.cli('codex','ordinary task')
        self.assertNotEqual(rc,0);reply=json.loads(out)
        self.assertEqual(reply['error'],'host_unreachable');self.assertEqual(reply['connect_attempts'],4)
        self.assertEqual(err.count('not connected'),3);self.assertFalse(self.received)
        self.server=await asyncio.start_unix_server(self.serve,path=self.st.sock_path)
        rc,out,_=await self.cli('status','run:012345abcdef')
        self.assertNotEqual(rc,0);self.assertEqual(json.loads(out)['status'],'unknown')
        self.assertFalse(self.started)

    async def test_notification_preference_off_keeps_queryable_failure_without_main_push(self):
        self.host.preferences={'background_done':{'enabled':False}}
        self.raise_after_effect=True;self.auto_release=True
        key=await self.dispatch();await self.host._busy_native[key]['task']
        for receipt in busy.finished_items(self.st.root):
            self.host.busy.finished.append({**receipt,'foreign':False,'kind':'relay_failure'})
        self.notifier.tick()
        self.assertTrue(self.agent.q.empty())
        rc,out,_=await self.cli('status',key)
        self.assertEqual(json.loads(out)['status'],'unknown')

    async def queued_source_change(self, change):
        keys=[await self.dispatch() for _ in range(4)]
        await self.until(lambda:len(list(self.work.glob('started-*')))==4)
        text='queued winter lantern meadow'
        target=await self.dispatch(text)
        self.assertEqual(self.relay.runtime(self.host)['rows'][target]['status'],'queued')
        if change == 'incomplete':
            (self.work/'.env').mkdir()
        else:
            os.environ['CUSTOM_SECRET'] = text if change == 'prompt' else 'fixture-model'
        p=self.processes[keys[0]];p.stdin.write(b'x');await p.stdin.drain()
        await self.until(lambda:self.relay.runtime(self.host)['rows'][target]['status']=='refused'
                         or (self.work/('started-'+target)).exists())
        self.assertFalse((self.work/('started-'+target)).exists(),'late secret inventory change must prevent native child/HTTP entry')
        await self.host._busy_native[target]['task']
        self.assertEqual(len(self.started),4)
        self.assertFalse((self.work/('effect-'+target)).exists())
        self.assertFalse(any(v.get('key')==target for v in busy.read_items(self.st.root)))
        finished=[r for r in busy.finished_items(self.st.root) if r['key']==target]
        self.assertEqual(len(finished),1)
        rc,out,err=await self.cli('status',target)
        self.assertEqual(rc,0);self.assertEqual(json.loads(out)['status'],'refused')
        self.assertFalse(json.loads(out)['replay_allowed'])
        self.assertNotIn(text,out+err)
        self.assertNotIn('fixture-model',out+err)

    async def test_queued_prompt_becomes_environment_secret_before_slot_opens(self):
        await self.queued_source_change('prompt')

    async def test_queued_selected_model_becomes_secret_before_slot_opens(self):
        await self.queued_source_change('model')

    async def test_queued_secret_inventory_becomes_incomplete_before_slot_opens(self):
        await self.queued_source_change('incomplete')

    async def capacity_source_change(self, change):
        from agentj import agent_codex
        from unittest.mock import patch
        import threading
        entered=threading.Event();release=threading.Event();native_entries=[]
        self.runner.stop()  # Exercise the real busy_native.run capacity/dispatch boundary.
        work=self.work
        class NativeFixture:
            def __init__(self,*args,**kw):self.proc=None
            async def run_once(self,text):
                native_entries.append(text)
                proc=await asyncio.create_subprocess_exec(sys.executable,'-c',
                    "from pathlib import Path;Path('capacity-effect').write_text('one effect')",cwd=work)
                await proc.wait()
                return '',proc.returncode
        def wait_capacity(*args):
            entered.set()
            return release.wait(5)
        self.addCleanup(release.set)
        text='capacity autumn lantern meadow'
        with patch.object(agent_codex,'CodexAgent',NativeFixture),patch.object(busy,'wait_capacity',side_effect=wait_capacity):
            key=await self.dispatch(text)
            await self.until(entered.is_set)
            if change=='incomplete':
                (self.work/'.env').mkdir()
                other=self.work/'new-current-root';other.mkdir()
                self.host.agent_cfg={**self.agent.cfg,'dir':str(other)}
            else:
                os.environ['CUSTOM_SECRET']=text
            release.set()
            await self.host._busy_native[key]['task']
        self.assertFalse(native_entries,'capacity wait must not bypass final fresh disclosure gate')
        self.assertFalse((self.work/'capacity-effect').exists())
        self.assertEqual(self.relay.runtime(self.host)['rows'][key]['status'],'refused')
        finished=[r for r in busy.finished_items(self.st.root) if r['key']==key]
        self.assertEqual(len(finished),1)
        self.assertNotIn(text,json.dumps(finished))

    async def test_capacity_wait_prompt_becomes_secret_before_native_dispatch(self):
        await self.capacity_source_change('prompt')

    async def test_capacity_wait_captured_root_becomes_incomplete_before_native_dispatch(self):
        await self.capacity_source_change('incomplete')
