"""Release regression: qualify a real local OpenCode binary and its protected first prompt in an isolated HOME."""
import _hermetic
import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agentj import agent_opencode, opencode_binary, shared_risk
from agentj.serve import Host
from test_p155_switch_harness import mk_state


class NativeFirstPrompt(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get('AGENTJ_TEST_NATIVE_OPENCODE'), 'requires local native OpenCode release fixture')
    async def test_registered_guard_refuses_tool_and_the_formal_first_turn_replies(self):
        exe=Path(os.environ['AGENTJ_TEST_NATIVE_OPENCODE']).resolve()
        home=Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        project=home/'project'
        project.mkdir()
        fixture=project/'fixture.txt'
        fixture.write_text('fixture-unopened')
        env={'HOME':str(home),'PATH':'/usr/bin:/bin','XDG_CONFIG_HOME':str(home/'config'),'XDG_DATA_HOME':str(home/'data'),
             'XDG_CACHE_HOME':str(home/'cache'),'XDG_STATE_HOME':str(home/'state'),'AGENTJ_STATE_DIR':str(home/'host'),
             'AGENTJ_SERVICE_ENV_FILE':str(home/'no-service-env'),'AGENTJ_SKILL_LINK':'off','AGENTJ_HARNESS_PREPARE':'off',
             'OPENCODE_DISABLE_MODELS_FETCH':'1','OPENCODE_DISABLE_AUTOUPDATE':'1'}
        self.enterContext(patch.dict(os.environ,env,clear=True))
        requests=[]
        denials=[]
        replies=[]
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                req=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                tools=[t['function']['name'] for t in req.get('tools',[]) if 'function' in t]
                requests.append((req.get('model'),tools))
                name=next((n for n in tools if n=='read'),None)
                if len(requests)==1 and name:
                    delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_fixture','type':'function',
                           'function':{'name':name,'arguments':json.dumps({'filePath':str(fixture)})}}]}
                    finish='tool_calls'
                else:
                    delta={'role':'assistant','content':'可用'}
                    finish='stop'
                base={'id':'test','object':'chat.completion.chunk','model':req['model'],'created':1}
                body=''.join('data: '+json.dumps({**base,'choices':[{'index':0,'delta':d,'finish_reason':stop}]})+'\n\n'
                             for d,stop in ((delta,None),({},finish)))+'data: [DONE]\n\n'
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                self.wfile.write(body.encode())
        api=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=api.serve_forever,daemon=True).start()
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        cfgfile=home/'config/opencode/opencode.json'
        cfgfile.parent.mkdir(parents=True)
        cfgfile.write_text(json.dumps({'model':'openrouter/another','provider':{'qa':{'npm':'@ai-sdk/openai-compatible',
            'options':{'baseURL':f'http://127.0.0.1:{api.server_port}/v1','apiKey':'fixture'},'models':{'chosen':{}}}}}))
        st=mk_state(home/'host','opencode',model='qa/chosen',dir=str(project),fence=False)
        pin=opencode_binary.version(exe,'explicit')
        self.assertTrue(pin and pin['version'].startswith('2.'))
        host=Host(st,events='json',read_stdin=False)
        agent=agent_opencode.OpenCodeAgent(host,{**st.agent_config(),'opencode_binary':pin})
        host.agent=agent
        guard=shared_risk.NativePathGuard.hook_event
        async def guarded(instance,event):
            if event.get('hook_event_name')=='PreToolUse' and event.get('tool_name')=='Read':
                self.assertTrue(event.get('_agentj_path_authenticated'))
                self.assertEqual(event.get('session_id'),agent.sid)
                denials.append(True)
                return {'hookSpecificOutput':{'permissionDecision':'deny','permissionDecisionReason':'Owner denied this fixture read'}}
            return await guard(instance,event)
        self.enterContext(patch.object(shared_risk.NativePathGuard,'hook_event',guarded))
        original=host.agent_text
        def capture(text,*args,**kwargs):
            replies.append(text)
            return original(text,*args,**kwargs)
        self.enterContext(patch.object(host,'agent_text',capture))
        try:
            await asyncio.wait_for(agent.turn('请回复可用。'),35)
            self.assertTrue(agent._control_channel.adapter.ready.is_set())
            self.assertEqual(agent._live_binary['version'],pin['version'])
            self.assertTrue(any('可用' in s for s in replies),replies)
            self.assertEqual(denials,[True])
            self.assertEqual({m for m,_ in requests},{'chosen'})
        finally:
            await agent.stop()
