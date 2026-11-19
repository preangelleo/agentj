"""Execute the shipped JS against both native hook API shapes without installing OpenCode or contacting a provider."""
import _hermetic
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from agentj import main_opencode_hook

DRIVER = r"""
import plugin from './.opencode/plugins/agentj-dispatch/index.js';
import assert from 'node:assert/strict';
const mode=process.argv[2], handlers={}, stored=new Map(), id='fixture';
const session={hook:async(key,callback)=>{handlers[key]=callback}};
if(mode==='entries'){
  session.instructions={entry:{
    put:async({sessionID,value})=>stored.set(sessionID,value),
    remove:async({sessionID})=>stored.delete(sessionID)
  }};
}
const tool=mode==='incomplete'?{}:{hook:async(key,callback)=>{handlers[key]=callback}};
const ctx={location:{directory:process.cwd()},session,tool};
if(mode==='incomplete'){
  await assert.rejects(plugin.setup(ctx),TypeError);
}else{
  await plugin.setup(ctx);
  await handlers.prompt({sessionID:id});
  const event={sessionID:id,tool:'read',input:{filePath:'read-fixture'}};
  await assert.rejects(handlers['execute.before'](event),/paired fixture denied/);
  async function context(sessionID){
    if(mode==='entries')return stored.get(sessionID);
    const x={sessionID,system:[]}; await handlers.context(x);
    return x.system[0]?.text;
  }
  assert.equal(await context(id),'dispatch notice');
  assert.equal(await context('another-session'),undefined);
  await handlers.prompt({sessionID:id});
  assert.equal(await context(id),undefined);
}
"""


class NativePluginContract(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,mode):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        path=root/'opencode.sock'
        events=[]
        async def receive(reader,writer):
            try:
                event=json.loads(await reader.readline())
                events.append(event)
                response={}
                if event['hook_event_name']=='PreToolUse':
                    response={'hookSpecificOutput':{'permissionDecision':'deny','permissionDecisionReason':'paired fixture denied',
                                                    'additionalContext':'dispatch notice'}}
                writer.write((json.dumps(response)+'\n').encode())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        listener=await asyncio.start_unix_server(receive,str(path))
        self.addAsyncCleanup(listener.wait_closed)
        self.addCleanup(listener.close)
        main_opencode_hook.install(root,path)
        (root/'package.json').write_text('{"type":"module"}')
        (root/'run.mjs').write_text(DRIVER)
        p=await asyncio.create_subprocess_exec('node','run.mjs',mode,cwd=root,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
        _,error=await asyncio.wait_for(p.communicate(),15)
        self.assertEqual(p.returncode,0,error.decode())
        if mode=='incomplete':
            self.assertEqual(events,[])
            return
        self.assertEqual([e['hook_event_name'] for e in events],['SharedGuardReady','UserPromptSubmit','PreToolUse','UserPromptSubmit'])
        self.assertEqual(events[2]['session_id'],'fixture')
        self.assertEqual(events[2]['tool_name'],'Read')
        self.assertEqual(events[2]['tool_input']['file_path'],'read-fixture')

    async def test_20_context_registration_denial_and_prompt_reset(self):
        await self.exercise('context')

    async def test_22_instruction_registration_denial_and_prompt_reset(self):
        await self.exercise('entries')

    async def test_missing_required_tool_hook_never_announces_readiness(self):
        await self.exercise('incomplete')
