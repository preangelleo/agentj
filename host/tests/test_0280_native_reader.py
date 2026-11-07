"""Actual Host._app routing with a blocked password-card stub; no OS or password."""
import _hermetic
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from agentj import approval_review, controls, native_scheduler_control as native, native_scheduler_plan, passkey
from agentj.serve import Host


class NativeReader(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']);self.addCleanup(self.tmp.cleanup)
        self.key=[b'fixture-public-signing-key'];self.pk={'id':'fixture-passkey','count':0}
        self.session=NS(device='fixture-phone',pub=b'fixture-public',sign_pub=self.key[0],cid=1,state='ready',p33=True)
        state=NS(root=Path(self.tmp.name),sign_key=lambda _:self.key[0],is_allowed=lambda _:True,
                 passkey_of_device=lambda _:self.pk,passkey_count=Mock())
        self.host=NS(st=state,channel='fixture-channel',agent=object(),hist=NS(epoch=1),
            task_epoch='task-epoch',_pairing_epoch=1,estop={'on':False},live_task='original-task',
            sessions={1:self.session},nonces=controls.Nonces(),_ctl_res=AsyncMock(),_phone_touch=Mock())
        # Real _current_context must clear a frozen origin_scope before this read.
        self.host._task_id=lambda:(approval_review.ORIGIN.get()or{}).get('fake_task',self.host.live_task)
        self.queued=[];self.running=[]
        self.host._bg=lambda coro,name:self.queued.append(coro)
        self.opened=asyncio.Event();self.reply=asyncio.Event();self.effects=[]
        async def password_card(authority):
            authority.guard();self.opened.set()
            await self.reply.wait()
            authority.guard();self.effects.append('synthetic')
            return {'native_result':{'ok':True}}
        async def answer(session,obj):
            self.assertIs(session,self.session);self.reply.set()
        self.host.elevate=NS(request_native_scheduler=AsyncMock(side_effect=password_card),on_phone=AsyncMock(side_effect=answer))
        self.host.native_scheduler_controller=NS(apply=Mock(side_effect=AssertionError('OS forbidden')))
        self.obj={'t':native.ACTION,'r':'fixture-rid-0001','n':'fixture-nonce','fa':{},
            'platform':'linux','source':'cron-system','scope':'system','id':'crontab:2',
            'snapshot':'0'*64,'op':'disable'}
        def prepare(controller,target,guard):guard();return {}
        def authority(token,host,session,controller,target,obj,guard,plan):
            return NS(guard=guard,origin=lambda:{'fake_task':'frozen-task'},close=Mock())
        for manager in (patch.object(controls,'check',return_value=None),patch.object(controls,'log'),
                        patch.object(passkey,'approval_fields',return_value={'id':self.pk['id']}),
                        patch.object(passkey,'verify_assertion',return_value=1),
                        patch.object(native,'_needs_privilege',return_value=True),
                        patch.object(native_scheduler_plan,'prepare',side_effect=prepare),
                        patch.object(native,'_NativePlan',side_effect=authority)):
            manager.start();self.addCleanup(manager.stop)

    async def asyncTearDown(self):
        for coro in self.queued:coro.close()
        for task in self.running:task.cancel()
        await asyncio.gather(*self.running,return_exceptions=True)

    async def enqueue(self):
        await asyncio.wait_for(Host._app(self.host,self.session,self.obj),.15)
        self.assertEqual(len(self.queued),1)

    async def run_queued(self):
        task=asyncio.create_task(self.queued.pop());self.running.append(task)
        await asyncio.wait_for(task,.3)

    async def test_real_app_returns_and_same_reader_can_answer_password_card(self):
        await self.enqueue()
        task=asyncio.create_task(self.queued.pop());self.running.append(task)
        await asyncio.wait_for(self.opened.wait(),.3)
        await asyncio.wait_for(Host._app(self.host,self.session,{'t':'elev_answer'}),.15)
        await asyncio.wait_for(task,.3)
        self.host.elevate.on_phone.assert_awaited_once()
        self.assertEqual(self.effects,['synthetic'])

    async def test_queued_key_change_never_opens_card(self):
        await self.enqueue();self.key[0]=b'replaced-fixture-key'
        await self.run_queued()
        self.host.elevate.request_native_scheduler.assert_not_awaited()
        self.assertEqual(self.effects,[])

    async def test_queued_real_task_change_cannot_hide_in_frozen_origin_scope(self):
        with approval_review.origin_scope({'fake_task':'frozen-task'}):
            await self.enqueue()
            self.host.live_task='replacement-task'
            await self.run_queued()
        self.host.elevate.request_native_scheduler.assert_not_awaited()

    async def test_queued_connection_replacement_never_adopts_new_session(self):
        await self.enqueue();self.host.sessions[1]=NS(**vars(self.session))
        await self.run_queued()
        self.host.elevate.request_native_scheduler.assert_not_awaited()

    async def test_queued_state_identity_change_never_opens_card(self):
        await self.enqueue();self.host.st=NS(**vars(self.host.st))
        await self.run_queued()
        self.host.elevate.request_native_scheduler.assert_not_awaited()
