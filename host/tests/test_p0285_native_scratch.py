"""Counterfactual trusted-owner classification only; no shell/model or real file enumeration."""
import _hermetic
import asyncio
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch
from agentj import native_scratch as ns,ordinary_maintenance as om,peer_origin,shared
import test_0280_ordinary_maintenance as fixtures


class Scratch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        repo=Path(__file__).resolve().parents[3]
        os.chdir(repo);self.addCleanup(os.chdir,str(repo))
        self.f=fixtures.Maintenance();await self.f.asyncSetUp();self.addCleanup(self.f.doCleanups)
        await asyncio.to_thread(self.f.base.harness)
        self.h=self.f.host
        actor=shared.SharedClaudeAgent(self.h,dict(self.h.agent_cfg))
        actor.cur_send=self.h.agent.cur_send
        self.h.agent=actor
        self.actor=actor
        self.sid=str(uuid.uuid4());cwd=self.h.agent_cfg['dir']
        actor.session={'sessionId':self.sid,'cwd':cwd,'pid':os.getpid(),'procStart':str(peer_origin._stat(Path('/proc'),os.getpid())[2])}
        slug=__import__('re').sub(r'[^A-Za-z0-9]','-',cwd)
        actor.transcript=Path(self.f.base.tmp.name)/'transcripts'/slug/(self.sid+'.jsonl')
        self.tmp_root=Path(self.f.base.tmp.name)/'native'
        self.seam=patch.object(ns,'_TMP',self.tmp_root);self.seam.start();self.addCleanup(self.seam.stop)
        self.root=self.tmp_root/('claude-'+str(os.getuid()))/slug/self.sid/'scratchpad'
        self.root.mkdir(parents=True)
        self.out=self.root/'preview';self.out.mkdir()
        self.image=Path(self.f.base.tmp.name)/'synthetic.png';self.image.write_bytes(b'PNG fixture')
        actor.phone_turn=actor.phone_seen=True
        actor.submit=lambda send:setattr(actor,'cur_send',send)
        await self.h._app(self.f.s,{'t':'msg','text':'Synthetic owner scratch maintenance'})
        self.scope=ns.Context(ns._TOKEN,self.h)
        self.assertIsNotNone(self.scope)

    def command(self):
        return f'ROOT="{self.root}"; OUT="$ROOT/preview"; rm -rf "$OUT"; mkdir -p "$OUT"; cd "$OUT" && cp "{self.image}" "$OUT/image.png" && find "$OUT" -maxdepth 1 -type f | wc -l'

    async def ask(self,command):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return await self.f.ask('Bash',{'command':command})

    async def test_registered_external_channels_stop_then_actual_phone_ingress(self):
        from types import SimpleNamespace
        from agentj import provenance,shared_turn_origin,notify
        channels=[('telegram-owner','telegram',provenance.telegram_owner(1001)),
                  ('telegram-group','telegram',provenance.telegram_group(-100,2002,'proxy',1001)),
                  ('wechat','wechat',provenance.wechat_owner()),
                  ('automation',None,provenance.automation('fixture-job'))]
        def native_echo():
            a=self.actor;send=a.cur_send
            source=provenance.normalize(send.source)
            a.phone_turn=True;a.phone_seen=False
            a.pending_source=a.native_source=source
            a.pending_text=provenance.body(send.text,source)
            a.native_request=(self.sid,send,a.pending_text)
            if not provenance.is_owner(source):a._mark_untrusted('pending')
            text=provenance.envelope('fixture',send.text,source)
            self.assertTrue(shared_turn_origin.delivered(a,{'isMeta':True,'origin':{'kind':'peer'}},text))
            a.phone_seen=True
        for label,kind,source in channels:
            with self.subTest(channel=label):
                if label=='automation':
                    self.assertEqual(notify.hand_to_agent(self.h,'fixture-job','fixture','synthetic data',[]),'queued')
                else:
                    session=SimpleNamespace(device=label,name='Synthetic external',cid=0,
                                            source_kind=kind,provenance=source)
                    await self.h._accept(session,'Synthetic external data',None)
                self.assertEqual(self.actor.cur_send.source,source)
                native_echo();self.assertTrue(self.actor.untrusted())
                self.actor._finish_stop('synthetic completion')
                self.actor.cur_send=None;self.actor.phone_turn=False
                self.assertFalse(self.actor.untrusted())
                await self.h._app(self.f.s,{'t':'msg','text':'Synthetic fresh owner maintenance'})
                native_echo()
                self.assertEqual(self.actor.cur_send.source,provenance.phone())
                self.assertFalse(provenance.untrusted_turn(self.actor))
                self.assertEqual(self.actor.cur_send.approval_origin['channel'],'phone')
                result,cards=await self.ask(self.command())
                self.assertEqual(result['behavior'],'allow');self.assertEqual(cards,[])

    async def test_same_form_owner_actual_host_allows_without_card(self):
        result,cards=await self.ask(self.command())
        self.assertEqual(result['behavior'],'allow');self.assertEqual(cards,[])

    async def test_wrong_sid_fake_context_and_replaced_native_identity_do_not_qualify(self):
        inp={'command':self.command()}
        self.assertIsNone(om.qualify('Bash',inp,cwd=self.h.agent_cfg['dir'],_native_scratch={'root':str(self.root),'owner':True}))
        bad=self.command().replace(self.sid,str(uuid.uuid4()))
        self.assertIsNone(ns.qualify('Bash',{'command':bad},self.h.agent_cfg['dir'],self.scope))
        self.actor.session['procStart']='0'
        self.assertIsNone(ns.qualify('Bash',inp,self.h.agent_cfg['dir'],self.scope))

    async def test_effects_before_and_after_deletion_are_not_washed(self):
        base=self.command()
        bad=[f'cp "{self.image}" /tmp/other; '+base,
             f'mv ~/Documents "{self.out}"; '+base,
             base+'; rm -rf ~/Documents',base+'; rm -rf /tmp/fixture-second',base+'; env rm -rf /tmp/fixture-second',base+'; ./unknown-script',base+'; cat ~/.ssh/id_rsa',
             base+'; cp ~/.ssh/id_rsa "$OUT/key"',
             base.replace('ROOT="','ROOT="$(' ,1),
             base.replace('cp "'+str(self.image)+'"', 'cp "$AMBIENT"')]
        for c in bad:
            with self.subTest(sha=__import__('hashlib').sha256(c.encode()).hexdigest()):
                self.assertIsNone(ns.qualify('Bash',{'command':c},self.h.agent_cfg['dir'],self.scope))

    async def test_symlink_hardlink_and_personal_destination_remain_rejected(self):
        link=self.root/'linked';link.symlink_to(self.out,target_is_directory=True)
        self.assertIsNone(ns.qualify('Bash',{'command':self.command().replace('/preview','/linked')},self.h.agent_cfg['dir'],self.scope))
        hard=self.root/'linked-file';os.link(self.image,hard)
        self.assertIsNone(ns.qualify('Bash',{'command':self.command().replace('/preview','/linked-file')},self.h.agent_cfg['dir'],self.scope))
        hard.unlink()
        self.assertIsNone(ns.qualify('Bash',{'command':self.command().replace(str(self.root),str(self.f.home/'Documents'))},self.h.agent_cfg['dir'],self.scope))

    async def test_private_proof_expires_on_same_send_or_root_identity_change(self):
        proof=om.qualify('Bash',{'command':self.command()},cwd=self.h.agent_cfg['dir'],_native_scratch=self.scope)
        self.assertIsNotNone(proof);self.assertTrue(om.still_valid(proof))
        self.out.rename(self.root/'previous');self.out.mkdir()
        self.assertFalse(om.still_valid(proof))

    async def test_standard_cannot_borrow_the_new_fullaccess_profile(self):
        self.h.preferences={**self.h.preferences,'approval':{**self.h.preferences.get('approval',{}),'level':'standard'}}
        self.assertIsNone(ns.context(self.h))
        self.assertFalse(self.scope.live())

    async def test_host_context_replacement_and_guest_do_not_borrow_owner_scope(self):
        self.assertTrue(self.scope.live())
        send=self.actor.cur_send
        self.actor.cur_send=None
        self.assertFalse(self.scope.live())
        self.actor.cur_send=send
        old=self.actor.transcript;self.actor.transcript=None
        self.assertFalse(self.scope.live())
        self.actor.transcript=old
        original=dict(self.actor.session)
        self.actor.session['sessionId']=str(uuid.uuid4())
        self.assertFalse(self.scope.live())
        self.actor.session.clear();self.actor.session.update(original)
        self.assertTrue(self.scope.live())
        oldsource=send.source;send.source={'kind':'email','role':'group','sender':'fixture@example.invalid'}
        self.assertFalse(self.scope.live())
        send.source=oldsource

    async def test_original_alias_guard_and_positional_ambient_are_not_bypassed(self):
        self.assertIsNone(ns.qualify('exec_command',{'command':self.command(),'cmd':'rm -rf ~/Documents'},self.h.agent_cfg['dir'],self.scope))
        for tail in ('cp "$1" "$OUT/file"','cp "$@" "$OUT/file"'):
            self.assertIsNone(ns.qualify('Bash',{'command':self.command()+'; '+tail},self.h.agent_cfg['dir'],self.scope))

    def launch(self,tid='own'):
        from agentj import shared_turn_origin
        event={'hook_event_name':'PostToolUse','session_id':self.sid,'tool_use_id':'call-'+tid,'tool_name':'Bash',
               'tool_input':{'command':'sleep 9'},'tool_response':{'backgroundTaskId':tid}}
        self.actor._note_background('PreToolUse',{**event,'hook_event_name':'PreToolUse'})
        self.actor._note_background('PostToolUse',event)
        from agentj import native_owner_launch
        with native_owner_launch.board_scope(self.actor,event):
            self.h.busy.hook_event(event,scope='shared-claude')
        shared_turn_origin.committed_launch(self.actor,event)
        return self.actor.cur_send

    def stop_and_complete(self,ids=('own',),successful=True):
        from agentj import shared_turn_origin,provenance
        # Actual Shared Stop retires the foreground. The following None is the
        # normal Agent.run finally seam, not a phone Send retained to obtain allow.
        with patch.object(self.actor,'read_transcript'):
            self.actor._finish_stop('Synthetic native Stop')
        self.actor.cur_send=None;self.actor.phone_turn=False
        self.actor._host_turn('running')
        trusted=shared_turn_origin.notification_trusted(self.actor,ids) and all(self.h.busy.launched.get(f'shared-claude:{self.sid}:{tid}') is False for tid in ids)
        shared_turn_origin.completed(self.actor,list(ids),trusted,successful=successful)
        self.assertIsNone(self.actor.cur_send)
        self.assertEqual(provenance.unattended_turn(self.actor),'system')

    async def test_real_owner_launch_stop_completed_host_ask_without_current_send(self):
        self.launch();self.stop_and_complete()
        result,cards=await self.ask(self.command())
        self.assertEqual(result['behavior'],'allow');self.assertEqual(cards,[])

    async def test_unknown_replay_and_cancelled_completion_never_borrow_owner(self):
        from agentj import shared_turn_origin,provenance
        owner=self.launch()
        self.stop_and_complete(ids=('unknown',))
        self.assertIsNone(ns.context(self.h))
        # A fresh original owner launch is available only after an actual new
        # paired owner ingress; completed display origins cannot supply it.
        with patch.object(self.actor,'read_transcript'):self.actor._finish_stop('Synthetic background turn end')
        self.actor.phone_turn=self.actor.phone_seen=True
        await self.h._app(self.f.s,{'t':'msg','text':'Synthetic next owner task'})
        owner=self.launch('next')
        self.assertIsNotNone(self.actor._native_task_origins[f'shared-claude:{self.sid}:next']['owner_launch'])
        owner.state='cancelled'
        self.stop_and_complete(ids=('next',))
        self.assertIsNone(ns.context(self.h))
        owner.state='done'
        shared_turn_origin.completed(self.actor,['next'],True,successful=True)
        self.assertIsNone(ns.context(self.h),'single-use completion')

    async def test_launch_key_pairing_generation_epoch_and_display_only_are_not_authority(self):
        from agentj import native_owner_launch,shared_turn_origin
        self.launch();self.stop_and_complete()
        binding=native_owner_launch.current(self.actor)
        self.assertIsNotNone(binding)
        scope=ns.context(self.h);self.assertIsNotNone(scope)
        self.h._pairing_epoch=object()
        self.assertFalse(scope.live());self.assertIsNone(native_owner_launch.current(self.actor))
        # prepare may carry a display origin, but it explicitly clears the exact
        # completion holder and must never become a scratch grant.
        from agentj import provenance
        self.actor.cur_send=__import__('agentj.compose',fromlist=['Send']).Send('say','system',0,text='Synthetic system')
        self.actor.cur_send.source=provenance.system()
        shared_turn_origin.prepare(self.actor,provenance.system())
        self.assertIsNone(native_owner_launch.current(self.actor))

    async def test_guest_launch_and_mixed_completion_do_not_inherit_owner(self):
        from agentj import provenance
        from agentj.compose import Send
        self.launch('owner')
        guest=Send('say','guest',0,text='Synthetic guest data')
        guest.source=provenance.email_guest('fixture@example.invalid')
        self.actor.cur_send=guest;self.actor.phone_turn=False
        self.actor.pending_source=self.actor.native_source=guest.source
        self.actor._mark_untrusted('running',True)
        self.launch('guest')
        self.stop_and_complete(ids=('owner','guest'))
        self.assertIsNone(ns.context(self.h))

    async def test_failed_status_and_replaced_epoch_never_inherit_owner(self):
        self.launch();self.stop_and_complete(successful=False)
        self.assertIsNone(ns.context(self.h))
        with patch.object(self.actor,'read_transcript'):self.actor._finish_stop('Synthetic background turn end')
        self.actor.phone_turn=self.actor.phone_seen=True
        await self.h._app(self.f.s,{'t':'msg','text':'Synthetic new launch'})
        self.launch('next');self.stop_and_complete(ids=('next',))
        scope=ns.context(self.h);self.assertIsNotNone(scope)
        self.h.hist.epoch=str(self.h.hist.epoch)+'-different'
        self.assertFalse(scope.live())

    async def test_original_launch_device_revocation_cannot_hide_behind_none_current_send(self):
        launch=self.launch();self.stop_and_complete()
        scope=ns.context(self.h);self.assertIsNotNone(scope)
        self.assertIsNone(self.actor.cur_send)
        self.assertTrue(self.h.st.remove_device(launch.device))
        self.assertFalse(scope.live());self.assertIsNone(ns.context(self.h))

    async def test_late_guest_post_cannot_use_new_owner_for_display_or_system_classification(self):
        from agentj import provenance,native_owner_launch,shared_turn_origin
        from agentj.compose import Send
        event={'hook_event_name':'PreToolUse','session_id':self.sid,'tool_use_id':'late-guest',
               'tool_name':'Bash','tool_input':{'command':'sleep 9'}}
        guest=Send('say','guest',0,text='Synthetic guest')
        guest.source=provenance.email_guest('fixture@example.invalid')
        self.actor.cur_send=guest;self.actor.phone_turn=False
        self.actor.native_source=self.actor.pending_source=guest.source
        self.actor._mark_untrusted('running',True)
        self.actor._note_background('PreToolUse',event)
        with patch.object(self.actor,'read_transcript'):self.actor._finish_stop('Synthetic guest Stop')
        self.actor.phone_turn=self.actor.phone_seen=True
        await self.h._app(self.f.s,{'t':'msg','text':'Synthetic later owner'})
        post={**event,'hook_event_name':'PostToolUse','tool_response':{'backgroundTaskId':'late'}}
        self.actor._note_background('PostToolUse',post)
        with native_owner_launch.board_scope(self.actor,post):self.h.busy.hook_event(post,scope='shared-claude')
        shared_turn_origin.committed_launch(self.actor,post)
        key=f'shared-claude:{self.sid}:late'
        self.assertTrue(self.h.busy.launched[key])
        self.assertIsNone(self.actor._native_task_origins[key]['origin'])
        self.assertFalse(shared_turn_origin.notification_trusted(self.actor,['late']))
        self.stop_and_complete(ids=('late',))
        self.assertIsNone(shared_turn_origin.snapshot(self.actor));self.assertIsNone(ns.context(self.h))

    async def test_native_taskstop_tombstones_late_and_replayed_completion(self):
        from agentj import shared_turn_origin,native_owner_launch
        self.launch('stopped')
        self.actor._note_background('PostToolUse',{'session_id':self.sid,'tool_name':'TaskStop',
            'tool_use_id':'stop','tool_input':{'task_id':'stopped'},'tool_response':{}})
        self.stop_and_complete(ids=('stopped',))
        self.assertIsNone(native_owner_launch.current(self.actor));self.assertIsNone(ns.context(self.h))
        shared_turn_origin.completed(self.actor,['stopped'],True,successful=True)
        self.assertIsNone(native_owner_launch.current(self.actor))

    async def test_missing_or_changed_native_pre_call_has_no_launch_authority(self):
        from agentj import native_owner_launch,shared_turn_origin
        post={'session_id':self.sid,'tool_use_id':'missing','tool_name':'Bash',
              'tool_input':{'command':'sleep 9'},'tool_response':{'backgroundTaskId':'missing'}}
        self.actor._note_background('PostToolUse',post)
        self.assertIsNone(self.actor._native_task_origins[f'shared-claude:{self.sid}:missing']['origin'])
        event={**post,'tool_use_id':'changed'}
        self.actor._note_background('PreToolUse',event)
        self.actor._note_background('PostToolUse',{**event,'tool_input':{'command':'different'},'tool_response':{'backgroundTaskId':'changed'}})
        self.assertIsNone(self.actor._native_task_origins[f'shared-claude:{self.sid}:changed']['origin'])

    async def test_all_native_cancel_tools_tombstone_exact_tasks_before_completion(self):
        for tool,field in (('TaskStop','task_id'),('KillShell','shell_id'),('KillBash','bash_id')):
            self.launch(tool)
            key=f'shared-claude:{self.sid}:{tool}'
            self.assertIsNotNone(self.actor._native_task_origins[key]['owner_launch'])
            self.actor._note_background('PostToolUse',{'session_id':self.sid,'tool_name':tool,
                'tool_use_id':'stop-'+tool,'tool_input':{field:tool},'tool_response':{}})
            self.assertIsNone(self.actor._native_task_origins[key]['owner_launch'])
        self.stop_and_complete(ids=('TaskStop','KillShell','KillBash'))
        self.assertIsNone(ns.context(self.h))

    async def test_cancel_after_completion_generation_change_and_expiry_invalidate_holder(self):
        from agentj import native_owner_launch,shared_turn_origin
        self.launch();self.stop_and_complete()
        scope=ns.context(self.h);self.assertIsNotNone(scope)
        key=f'shared-claude:{self.sid}:own'
        generation=self.h.busy.native_starts[key]['generation']
        self.h.busy.native_starts[key]['generation']=generation+'-replaced'
        self.assertFalse(scope.live())
        self.h.busy.native_starts[key]['generation']=generation
        self.assertTrue(scope.live())
        self.actor._note_background('PostToolUse',{'session_id':self.sid,'tool_name':'KillShell',
            'tool_input':{'shell_id':'own'},'tool_response':{}})
        self.assertFalse(scope.live())
        self.assertIsNone(native_owner_launch.current(self.actor))
        shared_turn_origin.completed(self.actor,['own'],True,successful=True)
        self.assertIsNone(ns.context(self.h))

    async def test_session_end_and_expired_launch_cannot_be_completed_as_owner(self):
        from agentj import shared_turn_origin
        self.launch('expired')
        key=f'shared-claude:{self.sid}:expired'
        self.actor._native_task_origins[key]['owner_launch'].expires=0
        self.stop_and_complete(ids=('expired',))
        self.assertIsNone(ns.context(self.h))
        self.actor._note_background('SessionEnd',{'session_id':self.sid})
        shared_turn_origin.completed(self.actor,['expired'],True,successful=True)
        self.assertIsNone(ns.context(self.h))

    async def test_bounded_cancel_tombstone_overflow_fails_closed_until_session_end(self):
        self.launch();self.stop_and_complete()
        self.actor._owner_cancelled_tasks={str(i) for i in range(4096)}
        self.actor._note_background('PostToolUse',{'session_id':self.sid,'tool_name':'TaskStop',
            'tool_input':{'task_id':'new-cancel'},'tool_response':{}})
        self.assertIsNone(ns.context(self.h))
        self.assertTrue(self.actor._owner_cancel_overflow)
        self.actor._note_background('SessionEnd',{'session_id':self.sid})
        self.assertNotIn('_owner_cancel_overflow',self.actor.__dict__)
