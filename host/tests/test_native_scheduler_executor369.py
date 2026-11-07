"""Offline fixed executor fixtures. No sudo, passwords, or production scheduler I/O."""
import unittest

class Entry(unittest.TestCase):
    def test_dedicated_entry_exists(self):
        from agentj import elevate
        self.assertTrue(callable(getattr(elevate.Elevator, 'request_native_scheduler', None)))

import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock, Mock
from agentj import native_scheduler_control as native, native_scheduler_plan as plans
from agentj import native_scheduler_helper as helper, elevate
from test_native_scheduler_control369 import FakeState

TEST_TMP = Path(__file__).resolve().parents[3] / 'TEST_TMP'

class Kernel(unittest.TestCase):
    def setUp(self):
        TEST_TMP.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'crontab'
        self.raw=b'# preserve\n0 1 * * * root /fixture\n# tail\n'
        self.path.write_bytes(self.raw)
        self.st=FakeState(str(self.root))
        self.backend=native.Backend(platform='linux',system_directory=str(self.root),uid=os.geteuid(),run=lambda _:self.fail('OS process forbidden'))
        self.controller=native.Controller(self.st,self.backend)
        self.target=dict(platform='linux',source='cron-system',scope='system',id='crontab:2',op='disable',snapshot='0'*64)
        self.kernel=helper.fixture_kernel()

    def plan(self):
        self.target['snapshot']=self.backend.source_snapshot(self.target,readonly=True)['snapshot']
        return plans.prepare(self.controller,self.target,lambda:None)

    def execute(self,plan,change=None):
        def exchange(message):
            if 'ready' in message:
                journal=self.controller.journal()
                journal[plan['journal_key']]=dict(original=plan['original'],bytes=plan['original_bytes'],expected=message['ready'],before=plan['before'],pending=plan['target']['op'])
                self.st.write_private(self.controller.path,json.dumps(journal).encode())
                if change: change()
                return {'permit':message['challenge']}
            self.assertEqual(message['done'],self.backend.source_snapshot(self.target,readonly=True))
            return {'ack':True}
        return self.kernel['execute'](plan,native.digest(plan),exchange,str(self.root),os.geteuid())

    def test_disable_restore_exact_bytes_and_metadata(self):
        original=self.path.stat()
        plan=self.plan(); self.execute(plan)
        self.assertTrue(self.path.read_bytes().startswith(b'# preserve\n# agentj-disabled:'))
        self.assertTrue(self.path.read_bytes().endswith(b'# tail\n'))
        self.target['op']='restore'
        self.execute(self.plan())
        self.assertEqual(self.path.read_bytes(),self.raw)
        after=self.path.stat()
        self.assertEqual((after.st_mode,after.st_uid,after.st_gid),(original.st_mode,original.st_uid,original.st_gid))

    def test_changed_preimage_immediately_after_permit_refuses(self):
        with self.assertRaises(ValueError):
            self.execute(self.plan(),lambda:self.path.write_bytes(b'# foreign\n'))
        self.assertEqual(self.path.read_bytes(),b'# foreign\n')
        self.assertTrue(self.controller.journal())

    def test_changed_digest_path_owner_source_delta(self):
        plan=self.plan()
        for field,value in [('path','/arbitrary'),('stat',[0]*9),('desired_bytes',base64.b64encode(b'evil').decode()),('target',dict(plan['target'],source='unknown'))]:
            changed=copy.deepcopy(plan);changed[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.kernel['validate'](changed,native.digest(changed),str(self.root),os.geteuid())
        with self.assertRaises(ValueError):
            self.kernel['validate'](plan,'0'*64,str(self.root),os.geteuid())
        self.assertEqual(self.path.read_bytes(),self.raw)

    def test_missing_permit_is_no_mutation(self):
        plan=self.plan()
        with self.assertRaises(ValueError):
            self.kernel['execute'](plan,native.digest(plan),lambda _: {},str(self.root),os.geteuid())
        self.assertEqual(self.path.read_bytes(),self.raw)

    def test_fake_constructor_and_serialized_authority(self):
        with self.assertRaises(native.Refused):
            native._NativePlan(None,None,None,None,None,None,None,None)
        async def go():
            elevator=elevate.Elevator.__new__(elevate.Elevator);elevator.host=NS()
            with self.assertRaises(elevate.Refused):
                await elevator.request_native_scheduler({'nonce':'fake'})
        asyncio.run(go())

    def test_interpreter_rejects_writable_or_symlink_components(self):
        with patch.object(helper.Path,'resolve',return_value=Path('/usr/bin/python3-fixture')),patch.object(helper.Path,'lstat',return_value=NS(st_mode=0o100777,st_uid=0)):
            with self.assertRaises(ValueError):helper.trusted_interpreter()

    def test_anacron_disable_restore(self):
        self.path=self.root/'anacrontab'
        self.raw=b'# preserve\n1 5 fixture /fixture\n'
        self.path.write_bytes(self.raw)
        self.target.update(source='anacron',id='anacrontab:2')
        self.execute(self.plan());self.target['op']='restore';self.execute(self.plan())
        self.assertEqual(self.path.read_bytes(),self.raw)

    def test_replaced_endpoint_rejected_before_connect(self):
        endpoint=self.root/'replaced-endpoint'
        endpoint.write_bytes(b'not a socket')
        info=endpoint.stat()
        argv=['fixed',str(endpoint),str(info.st_dev),str(info.st_ino+1),str(info.st_uid),str(os.getpid()),'a'*64,'b'*64]
        with patch.object(self.kernel['sys'],'argv',argv),patch.object(self.kernel['os'],'geteuid',return_value=0):
            with self.assertRaises(ValueError):self.kernel['main']()
        self.assertEqual(self.path.read_bytes(),self.raw)

    def test_run_parts_disable_restore(self):
        directory=self.root/'cron.daily';directory.mkdir()
        self.path=directory/'fixture'
        fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o700)
        os.write(fd,b'#!/bin/sh\nexit 0\n');os.close(fd)
        self.raw=self.path.read_bytes();self.target['id']='cron.daily/fixture'
        self.execute(self.plan());self.assertFalse(self.path.exists())
        self.target['op']='restore';self.execute(self.plan())
        self.assertEqual(self.path.read_bytes(),self.raw)

class Pipeline(Kernel, unittest.IsolatedAsyncioTestCase):
    async def pipeline(self, change=None, journal_window=False):
        import socket
        import time
        from agentj import controls, wire, passkey, approval_special
        pk={'id':'fixture-pk','spki':'fixture-public','sc':0}
        self.st.passkey_of_device=lambda _:dict(pk)
        self.st.passkey_count=Mock()
        self.st.log=Mock()
        self.st.agent_config=lambda: {}
        session=NS(state='ready',device='device',pub=b'fixture',cid='cid')
        history=NS(epoch=1,get=lambda _:None)
        host=NS(st=self.st,channel='fixture-channel',sessions={'cid':session},nonces=controls.Nonces(),
                native_scheduler_controller=self.controller,task_epoch=1,_pairing_epoch=1,hist=history,
                agent=NS(kind='fixture'),estop={'on':False},cur_turn=1,lang='en',
                _ctl_res=AsyncMock(),stopped=lambda:False,_approvers=lambda:['device'],
                _online_approvers=lambda:['device'],push_notify=Mock(),agent_notice=Mock())
        host._task_id=lambda:host.cur_turn
        host.hist_add=lambda src,*_:dict(id=1,ts=1,src=src)
        elevator=elevate.Elevator.__new__(elevate.Elevator)
        elevator.__dict__.update(host=host,st=self.st,cards={},bg=set(),completed={},review_requests=set(),review_operations={},sudo='stub-sudo',grants=NS(auto=AsyncMock(side_effect=AssertionError('no generic grants')),offer=Mock(side_effect=AssertionError('no grant offer'))))
        host.elevate=elevator
        self.target['snapshot']=self.backend.source_snapshot(self.target,readonly=True)['snapshot']
        request=dict(t=native.ACTION,r='1'*32,n='c'*32,ts=int(time.time()*1000),fa={},**self.target)
        request['sig']=wire.b64u(self.st.key.sign(controls.signed_message(host.channel,'device',native.ACTION,request['n'],request['ts'],controls.object_digest(native.ACTION,self.target))))
        checks=[]
        original_require=self.kernel['require']
        def checked(value):
            if not value:checks.append(__import__('sys')._getframe(1).f_lineno)
            return original_require(value)
        self.kernel['require']=checked
        calls=[]
        def process(argv,password,*args):
            self.assertEqual(argv[1:4],['-I','-S','-c'])
            self.assertEqual(argv[4],helper.BOOTSTRAP)
            self.assertNotIn(self.raw.decode(),str(argv))
            if change == 'noeffect':
                return dict(result='done',code=0,stdout='',stderr='',prompts=1)
            if change:
                if change=='restart':host._native_scheduler_plans.clear()
                elif change=='task':host.cur_turn=2
                elif change=='epoch':history.epoch=2
                elif change=='controller':host.native_scheduler_controller=object()
                elif change=='disconnect':session.state='closed'
                elif change=='unbind':host._unbinding_all=True
                elif change=='key':self.st.allowed=False
                elif change=='uv':pk['spki']='replacement'
                elif change=='preimage':self.path.write_bytes(b'# foreign\n')
                elif change=='bootstrap':helper.BOOTSTRAP+='\n# replaced'
                elif change=='digest':next(iter(host._native_scheduler_plans.values())).plan['destination']='different'
                elif change=='expiry':next(iter(host._native_scheduler_plans.values())).expires=0
                elif change=='pub':session.pub=b'replacement'
                elif change=='history':host.hist=NS(epoch=1)
                elif change=='state':host.st=object()
            endpoint,dev,ino,uid,host_pid,nonce,digest=argv[5:]
            self.assertEqual(int(host_pid),os.getpid())
            info=os.lstat(endpoint)
            self.assertEqual((info.st_dev,info.st_ino,info.st_uid),(int(dev),int(ino),int(uid)))
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(3);sock.connect(endpoint)
                with sock.makefile('rwb',buffering=0) as stream:
                    self.kernel['send'](stream,{'nonce':nonce,'digest':digest,'hello':'a'*64})
                    try:
                        reply=self.kernel['receive'](stream)
                    except (ValueError,OSError):
                        return dict(result='failed',code=1,stdout='',stderr='')
                    self.assertEqual(reply['hello'],'a'*64)
                    def exchange(value):
                        self.kernel['send'](stream,value)
                        return self.kernel['receive'](stream)
                    self.kernel['execute'](reply['plan'],digest,exchange,str(self.root),os.geteuid())
            # The consumed endpoint nonce cannot authorize a second connection.
            with socket.socket(socket.AF_UNIX) as replay:
                replay.settimeout(3);replay.connect(endpoint)
                with replay.makefile('rwb',buffering=0) as stream:
                    self.kernel['send'](stream,{'nonce':nonce,'digest':digest,'hello':'b'*64})
                    self.assertEqual(stream.readline(),b'')
            calls.append(argv)
            return dict(result='done',code=0,stdout='',stderr='',prompts=1)
        def sealed(*_):
            if change and change.startswith('password_'):
                if change=='password_task':host.cur_turn=2
                elif change=='password_key':self.st.allowed=False
                elif change=='password_uv':pk['spki']='replacement'
            return bytearray(b'stub')
        saved, release = threading.Event(), threading.Event()
        observed = asyncio.Event()
        watch_errors = []
        original_write = self.st.write_private
        original_live = native._NativePlan.live
        def paused_write(path, data):
            original_write(path, data)
            if journal_window and not saved.is_set():
                saved.set()
                if not release.wait(3):raise RuntimeError('journal fixture release timeout')
        def watched(authority, *args, **kwargs):
            in_window = (journal_window and saved.is_set() and not release.is_set()
                         and __import__('sys')._getframe(1).f_code.co_name == 'watch')
            if in_window and not observed.is_set():
                if change == 'journal_unknown':original_write(self.controller.path, b'{"foreign":true}')
                elif change == 'journal_key':self.st.allowed=False
                elif change == 'journal_task':host.cur_turn=2
                elif change == 'journal_preimage':self.path.write_bytes(b'# foreign\n')
            try:return original_live(authority, *args, **kwargs)
            except Exception as exc:
                if in_window:watch_errors.append(type(exc).__name__)
                raise
            finally:
                if in_window:observed.set()
        async def release_written():
            if not journal_window:return
            try:
                await asyncio.wait_for(observed.wait(), 2)
                if change:self.assertEqual(watch_errors, ['Refused'])
                else:self.assertEqual(watch_errors, [])
            finally:release.set()
        async def deliver(render,only=None,rid=None):
            c=elevator.cards.get(rid)
            if c is None or c.get('busy'): return
            self.assertEqual(only,'device');self.assertEqual(c['helper'],[]);self.assertIsNone(c['grant'])
            c.update(busy=True,cancel=threading.Event())
            try:
                await elevator._execute_approved(c,session,{'ct':wire.b64u(b'stub'),'epk':wire.b64u(b'x'*32),'ts':1},
                {'sk':wire.b64u(self.st.sign_key('device'))},c['nonce'],c['priv'])
            except approval_special.AuthorityChanged:
                elevator._finish(c,{'result':'unknown','why':'authority_changed'})
        with patch.object(native,'_needs_privilege',return_value=True),patch.object(native,'_root_helper_peer',return_value=True),\
             patch.object(passkey,'approval_fields',return_value={'id':pk['id']}),patch.object(passkey,'verify_assertion',return_value=1),\
             patch.object(approval_special,'attach'),patch.object(elevate,'locked_for',return_value=0),\
             patch.object(elevate,'audit'),patch.object(elevate,'note_password'),patch.object(elevate,'open_sealed',side_effect=sealed) ,\
             patch.object(helper,'trusted_interpreter',return_value='/trusted/fixture-python'),patch.object(helper,'BOOTSTRAP',helper.BOOTSTRAP),patch.object(elevate,'run_sudo',side_effect=process),\
             patch.object(elevator,'_deliver_card',side_effect=deliver),\
             patch.object(self.st,'write_private',side_effect=paused_write),\
             patch.object(native._NativePlan,'live',watched):
            observer=asyncio.create_task(release_written())
            try:
                await asyncio.wait_for(native.on_phone(host,session,request),5)
                await observer
            finally:
                release.set()
                if not observer.done():observer.cancel()
                await asyncio.gather(observer,return_exceptions=True)
        if change:
            self.assertFalse(host._ctl_res.call_args.kwargs.get('ok',False))
            self.assertEqual(self.path.read_bytes(),b'# foreign\n' if change in ('preimage','journal_preimage') else self.raw)
            return
        self.assertEqual(len(calls),1, checks)
        self.assertTrue(host._ctl_res.call_args.kwargs['ok'],host._ctl_res.call_args)
        self.assertTrue(self.path.read_bytes().startswith(b'# preserve\n# agentj-disabled:'))
        self.assertIsNone(next(iter(self.controller.journal().values()))['pending'])
        self.assertEqual(host._native_scheduler_plans,{})

    async def test_signed_uv_native_real_elevator_and_socket_helper(self):
        await self.pipeline()

    async def test_process_exit_zero_without_helper_effect_is_not_success(self):
        await self.pipeline('noeffect')

    async def test_atomic_journal_saved_before_thread_completion_keeps_live(self):
        await self.pipeline(journal_window=True)

    async def test_journal_transition_keeps_external_changes_fail_closed(self):
        for change in ('journal_unknown','journal_key','journal_task','journal_preimage'):
            with self.subTest(change=change):
                self.setUp()
                await self.pipeline(change, journal_window=True)
                if change == 'journal_unknown':self.assertEqual(self.controller.journal(), {'foreign':True})

    async def test_live_revocation_during_helper_wait(self):
        for change in ('restart','task','epoch','controller','disconnect','unbind','key','uv','preimage','bootstrap','digest','expiry','pub','history','state','password_task','password_key','password_uv'):
            with self.subTest(change=change):
                self.setUp()
                await self.pipeline(change)

class FullHost(unittest.IsolatedAsyncioTestCase):
    async def test_real_host_review_elevator_signed_uv_helper_completion(self):
        """Retained for the complete-dependency runner; no external I/O permitted."""
        import socket
        import time
        from agentj import controls, wire, passkey, approval_jev
        from test_l1 import _state, _ready
        from _p541_device import _owner_host, Phone
        from test_f17 import answer
        TEST_TMP.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as folder:
            root=Path(folder);path=root/'crontab'
            raw=b'# preserve\n0 1 * * * root /fixture\n'
            path.write_bytes(raw)
            st=_state(folder);sent=[]
            host=_owner_host(st,sent)
            phone=Phone(st,with_passkey=True);session=_ready(host,phone);session.p33=True
            host.push_notify=Mock();host._remind_offline=Mock();host.agent_notice=Mock()
            backend=native.Backend(platform='linux',system_directory=folder,uid=os.geteuid(),run=Mock(side_effect=AssertionError('no manager processes')))
            controller=native.Controller(st,backend);host.native_scheduler_controller=controller
            target=dict(platform='linux',source='cron-system',scope='system',id='crontab:2',op='disable',snapshot='0'*64)
            target['snapshot']=backend.source_snapshot(target,readonly=True)['snapshot']
            nonce='d'*32;ts=int(time.time()*1000)
            challenge=passkey.elevate_challenge(host.channel,phone.did,nonce,native.ACTION,nonce,controls.object_digest(native.ACTION,target))
            request=dict(t=native.ACTION,r='2'*32,n=nonce,ts=ts,fa=phone.auth.assert_(challenge,b'0',nonce=b'0'),**target)
            request['sig']=wire.b64u(phone.sk.sign(controls.signed_message(host.channel,phone.did,native.ACTION,nonce,ts,controls.object_digest(native.ACTION,target))))
            kernel=helper.fixture_kernel();calls=[];checks=[]
            original_require=kernel['require']
            def checked(value):
                if not value:checks.append(__import__('sys')._getframe(1).f_lineno)
                return original_require(value)
            kernel['require']=checked
            def process(argv,password,*_):
                self.assertEqual(argv[1:5],['-I','-S','-c',helper.BOOTSTRAP])
                endpoint,dev,ino,uid,pid,plan_nonce,digest=argv[5:]
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(3);sock.connect(endpoint)
                    with sock.makefile('rwb',buffering=0) as stream:
                        kernel['send'](stream,{'nonce':plan_nonce,'digest':digest,'hello':'a'*64})
                        response=kernel['receive'](stream)
                        def exchange(value):
                            kernel['send'](stream,value);return kernel['receive'](stream)
                        kernel['execute'](response['plan'],digest,exchange,folder,os.geteuid())
                calls.append(True)
                return dict(result='done',code=0,stdout='',stderr='',prompts=1)
            with patch.object(native,'_needs_privilege',return_value=True),patch.object(native,'_root_helper_peer',return_value=True),\
                 patch.object(helper,'trusted_interpreter',return_value='/fixture/python3'),patch.object(elevate,'run_sudo',side_effect=process),\
                 patch.object(approval_jev,'available',return_value=False):
                task=asyncio.create_task(native.on_phone(host,session,request))
                try:
                    for _ in range(200):
                        frames=[frame for _,frame in sent if frame.get('t')=='elev']
                        if frames or task.done():break
                        await asyncio.sleep(.005)
                    self.assertTrue(frames,task.result() if task.done() else 'no card')
                    frame=frames[-1]
                    self.assertEqual(frame['envelope']['source']['channel'],'phone')
                    self.assertTrue(frame['envelope']['source']['verified'])
                    self.assertEqual(frame.get('helper',[]),[])
                    await host.elevate.on_phone(session,answer(phone,frame,True,'fixture-password'))
                    await asyncio.wait_for(task,5)
                    self.assertEqual(len(calls),1, checks)
                    self.assertTrue(path.read_bytes().startswith(b'# preserve\n# agentj-disabled:'))
                    self.assertIsNone(next(iter(controller.journal().values()))['pending'])
                finally:
                    if not task.done():task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
