"""S0300-1: exact login-spawn boundaries after real settings UV; no external login."""
import _hermetic
import asyncio
import copy
import unittest
from unittest.mock import patch, Mock
from test_0300_avon import Settings
from agentj import accounts, auth_rescue


class Window(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Settings.asyncSetUp
    request = Settings.request
    finish = Settings.finish

    def change(self, kind):
        h=self.h
        if kind=='device':h.st.remove_device(self.phone.did)
        elif kind=='session':h.sessions[self.session.cid]=copy.copy(self.session)
        elif kind=='epoch':h.hist.epoch='replacement-epoch'
        elif kind=='signing_key':h.st.add_device(self.session.pub,'Synthetic replacement',b'x'*32)
        elif kind=='passkey':
            from test_p57_passkey import Authenticator, pk_record
            self.assertTrue(h.st.set_passkey(self.session.pub,pk_record(Authenticator()),peer_ok=True))
        elif kind=='estop':h.estop['on']=True

    async def window(self, change=None):
        self.ar._codex=auth_rescue.Rescue._codex.__get__(self.ar,auth_rescue.Rescue)
        allocate=accounts.new_codex_slot
        allocated=[]
        def slot(root):
            iid,path=allocate(root);allocated.append(path)
            self.change(change)
            return iid,path
        started=Mock(side_effect=auth_rescue.RescueError('cancelled'))
        with patch.object(accounts,'new_codex_slot',side_effect=slot),patch.object(auth_rescue.CodexFlow,'start',started):
            task,a=await self.request()
            await self.h._answer(self.session,self.phone.answer(self.h._ask_msg(a,self.session),True))
            self.assertEqual(await task,{'ok':True})
            for _ in range(200):
                if self.ar.flow.get('stage')=='failed':break
                await asyncio.sleep(.005)
        self.assertEqual(started.call_count,0 if change else 1)
        self.assertTrue(allocated)
        self.assertTrue(all(not path.exists() for path in allocated),'only the newly allocated slot must be discarded')
        self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))

    async def test_device_revoked_during_slot_allocation(self):await self.window('device')
    async def test_session_replaced_during_slot_allocation(self):await self.window('session')
    async def test_epoch_replaced_during_slot_allocation(self):await self.window('epoch')
    async def test_signing_key_replaced_during_slot_allocation(self):await self.window('signing_key')
    async def test_passkey_replaced_during_slot_allocation(self):await self.window('passkey')
    async def test_estop_during_slot_allocation(self):await self.window('estop')
    async def test_unchanged_settings_start_once_and_cleanup_own_failed_slot(self):await self.window()

    async def test_actual_codex_spawn_consumes_subject_once_after_environment_preparation(self):
        from agentj.list_approval import SettingsContext
        _,slot=accounts.new_codex_slot(self.h.st.root)
        context=SettingsContext.capture(self.h,self.ar,self.session,{'summary':'Synthetic Codex sign in'})
        spawn=Mock(side_effect=auth_rescue.RescueError('cancelled'))
        async def work():
            await self.ar._start_command(auth_rescue.CodexFlow(home=slot).start)
        with patch.object(auth_rescue,'binary',return_value='/synthetic/codex'), \
                patch.object(auth_rescue,'harness_env',return_value={}),patch.object(auth_rescue.subprocess,'Popen',spawn):
            with self.assertRaises(auth_rescue.RescueError):
                await self.ar._confirmed_start(work(),context,'codex')
        spawn.assert_called_once()
        self.assertFalse(context.live(self.h));self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))

    async def test_all_three_official_flow_spawns_recheck_after_environment_preparation(self):
        from agentj.list_approval import SettingsContext
        _,slot=accounts.new_codex_slot(self.h.st.root)
        for factory in (lambda:auth_rescue.CodexFlow(home=slot),lambda:auth_rescue.KimiFlow(),
                        lambda:auth_rescue.ClaudeFlow('setup_token')):
            self.h.estop['on']=False
            context=SettingsContext.capture(self.h,self.ar,self.session,{'summary':'Synthetic official sign in'})
            def environment(*args):
                self.h.estop['on']=True
                return {}
            async def work():await self.ar._start_command(factory().start)
            with patch.object(auth_rescue,'binary',return_value='/synthetic/official-cli'), \
                    patch.object(auth_rescue,'harness_env',side_effect=environment), \
                    patch.object(auth_rescue.subprocess,'Popen') as spawn,patch.object(auth_rescue,'Pty') as pty:
                with self.assertRaisesRegex(auth_rescue.RescueError,'session_changed'):
                    await self.ar._confirmed_start(work(),context,'codex')
            spawn.assert_not_called();pty.assert_not_called()
            self.assertFalse(self.h.__dict__.get('_auth_settings_requests'))
