"""Native JSON-RPC readonly denial -> fixed UV app action, never accept/run original command."""
import _hermetic
import asyncio
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import shlex
from unittest.mock import patch
from agentj import provenance
from agentj.agent_codex import CodexAgent
import test_0311_friends_tell_card as app_fixture


class NativeJourney(app_fixture.Journey):
    for _method in [name for name in dir(app_fixture.Journey) if name.startswith('test_')]:
        locals()[_method]=None
    del _method

    async def native_request(self,flag=True,outcome='allow',source=None,command=None,during=None,research=False,mode=None):
        actor=CodexAgent(self.host,dict(self.actor.cfg))
        actor.tid='friends-native-thread';actor.turn_id='friends-native-turn'
        actor.native_source=source or provenance.terminal();actor.research=research
        if mode:actor.cfg['mode']=mode
        actor.thread={'sandbox':{'type':'readOnly'},'approvalPolicy':'on-request'}
        self.host.agent=self.actor=actor;self.host.agent_cfg=actor.cfg
        receipt=self.e.root/'native-reply.json'
        cmd=command or shlex.join(['agentj','friends','tell',self.fid,'Synthetic fixed message',*(['--sandbox-card'] if flag else [])])
        request={'jsonrpc':'2.0','id':'native-friends-request','method':'item/commandExecution/requestApproval',
                 'params':{'threadId':actor.tid,'turnId':actor.turn_id,'itemId':'one-call',
                           'command':cmd,'cwd':str(self.work)}}
        code="import json,sys;from pathlib import Path;print(sys.argv[1],flush=True);v={}\nwhile v.get('id')!='native-friends-request':v=json.loads(sys.stdin.readline())\nPath(sys.argv[2]).write_text(json.dumps(v))"
        proc=await asyncio.create_subprocess_exec(os.sys.executable,'-u','-c',code,json.dumps(request),str(receipt),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=self.env)
        actor.proc=proc
        async def cleanup():
            if proc.returncode is None:proc.kill();await proc.wait()
        self.addAsyncCleanup(cleanup)
        before=copy.deepcopy(actor.thread);start=len(self.cards());answered=False
        # The only child is the already-running stand-in native SDK above.
        # The original CLI/shell/browser command must never be launched.
        with patch('asyncio.create_subprocess_exec',side_effect=AssertionError('No shell/native child may be started by this action')):
            reader=asyncio.create_task(actor._read(proc))
            for _ in range(500):
                cards=self.cards()[start:]
                if cards and not answered:
                    answered=True
                    if during:
                        value=during(actor)
                        if hasattr(value,'__await__'):await value
                    if outcome in ('allow','deny','no_uv'):
                        answer=self.phone.answer(cards[-1],outcome!='deny')
                        if outcome=='no_uv':answer.pop('fa')
                        await self.host._app(self.s,answer)
                if receipt.exists():break
                await asyncio.sleep(.01)
            await asyncio.wait_for(reader,5)
            if actor.tasks:await asyncio.gather(*tuple(actor.tasks))
        self.assertTrue(receipt.exists(),'native server must receive a final decline')
        response=json.loads(receipt.read_text());self.assertEqual(response['result'],{'decision':'decline'})
        self.assertEqual(actor.thread,before)
        return self.cards()[start:]

    async def test_readonly_native_flag_request_sends_only_fixed_message_but_original_rpc_is_declined(self):
        cards=await self.native_request()
        self.assertEqual(len(cards),1)
        if os.environ.get('AJ_FRIENDS_NATIVE_CARD_OUT'):
            Path(os.environ['AJ_FRIENDS_NATIVE_CARD_OUT']).write_text(json.dumps(cards[0],ensure_ascii=False))
        self.assertTrue(cards[0]['passkey_required']);self.assertEqual(cards[0]['tool'],'FriendsTellOnce')
        sent=self.service.net.of('pmsg');self.assertEqual(len(sent),1)
        self.assertEqual((sent[0][0],sent[0][1]['text']),(self.fid,'Synthetic fixed message'))

    async def test_original_native_request_without_flag_remains_declined_and_sends_nothing(self):
        cards=await self.native_request(flag=False)
        self.assertFalse(cards);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_denial_has_card_but_no_send(self):
        cards=await self.native_request(outcome='deny')
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_signed_answer_without_uv_has_no_send(self):
        cards=await self.native_request(outcome='no_uv')
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_research_cannot_get_app_card(self):
        self.assertFalse(await self.native_request(research=True));self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_no_tools_cannot_get_app_card(self):
        self.assertFalse(await self.native_request(mode='no_tools'));self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_system_data_cannot_get_app_card(self):
        self.assertFalse(await self.native_request(source=provenance.system()));self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_guest_cannot_get_app_card(self):
        self.assertFalse(await self.native_request(source=provenance.email_guest('synthetic@example.invalid')))
        self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_delegated_task_cannot_get_app_card(self):
        self.assertFalse(await self.native_request(source={'kind':'same_owner','role':'task'}))
        self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_turn_change_while_uv_waiting_prevents_effect(self):
        cards=await self.native_request(during=lambda a:setattr(a,'turn_id','another-turn'))
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_cancelled_request_while_uv_waiting_prevents_effect(self):
        def cancel(a):
            pending=a.pending['native-friends-request']
            pending.set_result(None)
        cards=await self.native_request(during=cancel)
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_new_send_while_uv_waiting_prevents_effect(self):
        cards=await self.native_request(during=lambda a:setattr(a,'cur_send',NS(source=provenance.phone())))
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))

    async def test_native_shell_suffix_is_not_a_fixed_app_intent(self):
        cmd=shlex.join(['agentj','friends','tell',self.fid,'Synthetic fixed message','--sandbox-card'])+' && echo unsafe'
        self.assertFalse(await self.native_request(command=cmd));self.assertFalse(self.service.net.of('pmsg'))

    async def test_duplicate_native_request_revokes_prior_waiting_capability(self):
        def duplicate(a):
            a._on_request({'jsonrpc':'2.0','id':'native-friends-request',
                'method':'item/commandExecution/requestApproval',
                'params':{'threadId':a.tid,'turnId':a.turn_id,'itemId':'duplicate-call',
                    'command':shlex.join(['agentj','friends','tell',self.fid,'Synthetic fixed message','--sandbox-card']),
                    'cwd':str(self.work)}})
        cards=await self.native_request(during=duplicate)
        self.assertEqual(len(cards),1);self.assertFalse(self.service.net.of('pmsg'))
