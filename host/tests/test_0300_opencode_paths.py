"""Exact OpenCode root config inventory and real paired UV, isolated dummy files."""
import _hermetic
from _testtmp import TEST_TMP
import asyncio
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from agentj import uv_list
from agentj.list_approval import NativeContext
from _p541_device import Phone, _owner_host
from test_l1 import _state, _ready


class Targets(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'project'
        self.cwd=self.project/'nested'/'deep';self.cwd.mkdir(parents=True)
        self.env={'HOME':str(self.root/'home'),'XDG_CONFIG_HOME':str(self.root/'config'),
                  'AGENTJ_STATE_DIR':str(self.root/'state')}

    def test_actual_cwd_and_ancestor_root_config_write_vectors(self):
        for name in ('opencode.json','opencode.jsonc'):
            for present in (False,True):
                path=self.project/name
                if present:path.write_text('{"permission":{"bash":"deny"}}')
                for tool,inp in (
                    ('Write',{'file_path':str(path),'content':'{}'}),
                    ('Edit',{'file_path':str(path),'old_string':'deny','new_string':'allow'}),
                    ('ApplyPatch',{'patch':'*** Begin Patch\n*** Update File: '+str(path)+'\n@@\n-deny\n+allow\n*** End Patch'}),
                    ('Bash',{'command':'printf x > ../../'+name}),
                ):
                    with self.subTest(name=name,present=present,tool=tool):
                        self.assertTrue(uv_list.required(tool,inp,cwd=str(self.cwd),env=self.env))
                if present:path.unlink()

    def test_current_directory_root_files_and_exact_inventory(self):
        paths={row['path'] for row in uv_list.protected_paths(cwd=str(self.cwd),env=self.env)}
        for ancestor in (self.cwd,*self.cwd.parents):
            for name in ('opencode.json','opencode.jsonc'):
                self.assertIn(str(ancestor/name),paths)
                self.assertTrue(uv_list.required('Write',{'file_path':str(ancestor/name)},cwd=str(self.cwd),env=self.env))

    def test_unloaded_same_names_reads_env_and_scratch_remain_ordinary(self):
        for name in ('opencode.json','opencode.jsonc'):
            for path in (self.root/'sibling'/name,self.cwd/'unloaded-subdirectory'/name):
                self.assertFalse(uv_list.required('Write',{'file_path':str(path),'content':'{}'},cwd=str(self.cwd),env=self.env))
            self.assertFalse(uv_list.required('Read',{'file_path':str(self.project/name)},cwd=str(self.cwd),env=self.env))
            self.assertFalse(uv_list.required('Bash',{'command':'cat ../../'+name},cwd=str(self.cwd),env=self.env))
        for tool,inp in (
            ('Write',{'file_path':str(self.project/'opencode.json.bak'),'content':'{}'}),
            ('Write',{'file_path':str(self.project/'package.json'),'content':'{}'}),
            ('Read',{'file_path':str(self.project/'.env')}),
            ('Bash',{'command':'cat .env'}),
            ('Bash',{'command':'rm -rf '+str(self.root/'scratch')}),
        ):
            self.assertFalse(uv_list.required(tool,inp,cwd=str(self.cwd),env=self.env),(tool,inp))


class PairedExecution(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TEST_TMP);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.project=self.root/'caller'
        self.cwd=self.project/'nested'/'deep';self.cwd.mkdir(parents=True)
        main=self.root/'main';main.mkdir()
        env=mock.patch.dict(os.environ,{'HOME':str(self.root/'home'),'XDG_CONFIG_HOME':str(self.root/'config')})
        env.start();self.addCleanup(env.stop)
        self.st=_state(str(self.root/'state'));self.sent=[];self.h=_owner_host(self.st,self.sent)
        self.h.ask_ttl=1;self.h.agent_cfg={'dir':str(main)}
        self.ph=Phone(self.st,with_passkey=True);self.ph.policy_host=self.h;self.s=_ready(self.h,self.ph)
        self.native=SimpleNamespace(kind='opencode',host=self.h,cfg={'dir':str(self.cwd)},
                                    sid='isolated-native-caller',proc=SimpleNamespace(pid=os.getpid()))
        self.effects=0
        self.tasks=[];self.addAsyncCleanup(self.stop)

    async def stop(self):
        for task in self.tasks:
            if not task.done():task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)

    async def launch(self,name='opencode.json',tool='Write'):
        target=self.project/name;target.write_text('original')
        path='../../'+name
        inp={'file_path':path,'content':'approved','cwd':str(self.root/'forged-input-cwd')}
        if tool=='Edit':inp={'file_path':path,'old_string':'original','new_string':'approved'}
        elif tool=='ApplyPatch':inp={'patch':'*** Begin Patch\n*** Update File: '+path+'\n@@\n-original\n+approved\n*** End Patch'}
        elif tool=='Bash':inp={'command':'printf approved > '+path}
        async def execute():
            # Fixed local fixture executor: the exact captured caller cwd and
            # requested path are used both for admission and the dummy write.
            context=NativeContext.capture(self.native)
            answer=await self.h.ask(tool,inp,_harness_permission=True,_native_context=context)
            if answer['behavior']=='allow':
                (Path(context.cwd)/path).resolve().write_text('approved')
                self.effects+=1
            return answer
        task=asyncio.create_task(execute());self.tasks.append(task)
        for _ in range(100):
            if self.h.asks or task.done():break
            await asyncio.sleep(.005)
        self.assertFalse(task.done(),task.result() if task.done() else '')
        ask=next(iter(self.h.asks.values()))
        self.assertTrue(ask.require_passkey)
        self.assertEqual(target.read_text(),'original');self.assertEqual(self.effects,0)
        return target,task,ask

    async def test_every_adapter_requires_fresh_uv_then_executes_once(self):
        for name in ('opencode.json','opencode.jsonc'):
            for tool in ('Write','Edit','ApplyPatch','Bash'):
                self.effects=0
                target,task,ask=await self.launch(name,tool)
                unsigned_uv=self.ph.answer(self.h._ask_msg(ask,self.s),True);unsigned_uv.pop('fa',None)
                await self.h._answer(self.s,unsigned_uv)
                self.assertFalse(ask.fut.done());self.assertEqual(target.read_text(),'original')
                approved=self.ph.answer(self.h._ask_msg(ask,self.s),True)
                await self.h._answer(self.s,approved)
                self.assertEqual((await task)['behavior'],'allow')
                self.assertEqual(target.read_text(),'approved');self.assertEqual(self.effects,1)
                await self.h._answer(self.s,approved)
                self.assertEqual(self.effects,1)

    async def test_expired_card_is_zero_effect(self):
        target,task,ask=await self.launch('opencode.jsonc')
        answer=self.ph.answer(self.h._ask_msg(ask,self.s),True)
        ask.deadline=time.monotonic()-1
        await self.h._answer(self.s,answer)
        self.assertFalse(ask.fut.done());self.assertFalse(ask.uv_ok)
        self.assertEqual(target.read_text(),'original');self.assertEqual(self.effects,0)

    async def test_revoked_signer_is_zero_effect(self):
        target,task,ask=await self.launch()
        answer=self.ph.answer(self.h._ask_msg(ask,self.s),True)
        self.assertTrue(self.st.remove_device(self.ph.did))
        await self.h._answer(self.s,answer)
        self.assertFalse(ask.fut.done());self.assertEqual(target.read_text(),'original');self.assertEqual(self.effects,0)

    async def test_remote_without_key_cannot_write(self):
        self.assertTrue(self.st.remove_device(self.ph.did))
        self.ph=Phone(self.st,with_passkey=False);self.ph.policy_host=self.h;self.s=_ready(self.h,self.ph,cid=12)
        target,task,ask=await self.launch('opencode.jsonc')
        await self.h._answer(self.s,self.ph.answer(self.h._ask_msg(ask,self.s),True))
        self.assertFalse(ask.fut.done());self.assertFalse(ask.uv_ok)
        self.assertEqual(target.read_text(),'original');self.assertEqual(self.effects,0)
