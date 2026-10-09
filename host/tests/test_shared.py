"""P40b explicit attachment regressions; local fixtures, never owner's sessions."""
import _hermetic
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentj import agent, shared, preferences

class Host:
    def __init__(self):
        self.st = Mock()
        self.inputs = []; self.replies = []; self.phone_replies = []; self.notices = []
    def desktop_input(self, text): self.inputs.append(text)
    def desktop_text(self, text): self.replies.append(text)
    def desktop_end(self): pass
    def agent_text(self, text): self.phone_replies.append(text)
    def agent_notice(self, text): self.notices.append(text)
    def agent_status(self, status): pass
    def local_notice(self, text): self.notices.append(text)

class ClaudeTransport(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='p40b-',dir='/var/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.work = self.root/'work'; self.work.mkdir()
        self.cc = self.root/'claude'; (self.cc/'sessions').mkdir(parents=True)
        self.env = patch.dict(os.environ, {'CLAUDE_CONFIG_DIR':str(self.cc)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.sock = socket.socket(socket.AF_UNIX); self.sock.bind(str(self.root/'peer.sock')); self.sock.listen()
        self.addCleanup(self.sock.close)
        self.sid = '11111111-1111-1111-1111-111111111111'
        self.session = {'pid':os.getpid(),'cwd':str(self.work),'sessionId':self.sid,'kind':'interactive',
                        'messagingSocketPath':str(self.root/'peer.sock'),'status':'idle','updatedAt':10}
        (self.cc/'sessions/owner.json').write_text(json.dumps(self.session))
        digest=hashlib.sha256(self.session['messagingSocketPath'].encode()).hexdigest()
        self.key=self.cc/'sessions'/f'{os.getpid()}.{digest}.key'
        self.token='TEST_ONLY_PEER_TOKEN'
        self.key.write_text(json.dumps({'peerToken':self.token})); self.key.chmod(0o600)
        slug=__import__('re').sub(r'[^A-Za-z0-9]','-',str(self.work))
        self.transcript=self.cc/'projects'/slug/(self.sid+'.jsonl')
        self.transcript.parent.mkdir(parents=True); self.transcript.write_text('')
        self.host=Host()
        mock_channel=patch.object(shared.SharedClaudeAgent,'prepare_channel',new=AsyncMock())
        mock_channel.start();self.addCleanup(mock_channel.stop)
        self.cfg={'kind':'claude','dir':str(self.work),'_workflow_ceo':True,'session_mode':'shared','high_risk_warnings':False}

    async def test_registry_rejects_wrong_folder_noninteractive_dead_pid(self):
        self.assertEqual(len(shared.claude_sessions(str(self.work))),1)
        self.assertEqual(shared.claude_sessions(str(self.root)),[])
        for change in ({'kind':'headless'},{'pid':-1},{'messagingSocketPath':'/does/not/exist'}):
            (self.cc/'sessions/owner.json').write_text(json.dumps({**self.session,**change}))
            self.assertEqual(shared.claude_sessions(str(self.work)),[])

    async def test_ambiguous_folder_requires_exact_session(self):
        second={**self.session,'sessionId':'22222222-2222-2222-2222-222222222222'}
        (self.cc/'sessions/second.json').write_text(json.dumps(second))
        a=shared.SharedClaudeAgent(self.host,self.cfg)
        self.assertFalse(await a.attach())
        a.cfg['shared_session_id']=self.sid
        self.assertTrue(await a.attach())

    async def test_first_phone_attach_waits_for_ordinary_start(self):
        a=shared.SharedClaudeAgent(self.host,self.cfg)
        starting=asyncio.Event();ready=asyncio.Event()
        async def ordinary():
            starting.set();await ready.wait()
        with patch.object(shared,'claude_sessions',side_effect=lambda *args: [self.session] if ready.is_set() else []), \
             patch.object(a,'ordinary_start',new=AsyncMock(side_effect=ordinary)) as launch:
            observer=asyncio.create_task(a.attach());await starting.wait()
            phone=asyncio.create_task(a.attach());await asyncio.sleep(0)
            self.assertFalse(phone.done())
            ready.set();self.assertEqual(await asyncio.gather(observer,phone),[True,True])
            launch.assert_awaited_once()

    async def test_relay_socket_protocol_keeps_auth_local(self):
        got=[]
        def accept():
            c,_=self.sock.accept()
            with c, c.makefile('r') as f:
                got.extend(json.loads(f.readline()) for _ in range(2))
        thread=threading.Thread(target=accept);thread.start()
        await asyncio.to_thread(shared.claude_send,self.session,'phone instruction')
        thread.join(2)
        self.assertEqual(got[0],{'type':'auth','token':self.token})
        self.assertIn('phone instruction',got[1]['message']['content'])
        self.assertNotIn(self.token,json.dumps(got[1]))
        self.assertEqual(self.key.read_text(),json.dumps({'peerToken':self.token}))
        self.assertEqual(got[1]['priority'],'next')

    async def test_transcript_new_desktop_turns_only_and_partial_lines(self):
        self.transcript.write_text(json.dumps({'type':'user','uuid':'old','message':{'content':'old'}})+'\n')
        a=shared.SharedClaudeAgent(self.host,self.cfg); self.assertTrue(await a.attach())
        records=[{'type':'user','uuid':'new','message':{'content':'desktop input'}},
                 {'type':'assistant','uuid':'reply','message':{'content':[{'type':'text','text':'desktop reply'}]}},
                 {'type':'user','uuid':'tool','message':{'content':[{'type':'tool_result','content':'never export tool output'}]}}]
        with self.transcript.open('a') as f:
            for r in records: f.write(json.dumps(r)+'\n')
            f.write('{"type":')
        a.read_transcript();a.read_transcript()
        self.assertEqual(self.host.inputs,['desktop input'])
        self.assertEqual(self.host.replies,[], 'transcript prose is not a committed reply')
        self.assertFalse(a.done.is_set(),'initial registry idle must not complete a pending phone turn')

    async def test_p87_meta_inputs_do_not_open_desktop_turn(self):
        a=shared.SharedClaudeAgent(self.host,self.cfg);await a.attach()
        records=[{'type':'user','uuid':str(i),'message':{'content':text},**extra} for i,(text,extra) in enumerate([
            ('skill body',{'isMeta':True}), ('Base directory for this skill: ~/.claude/skills/test\nbody',{}),
            ('<local-command-stdout>done</local-command-stdout>',{}),
            ('<local-command-caveat>commands</local-command-caveat>',{}),
            ('This session is being continued from a previous conversation that ran out of context.',{}),
            ('<system-reminder>hook</system-reminder>',{}), ('hook output',{'isMeta':True}),
            ('child',{'isSidechain':True}), ('real keyboard input',{})])]
        records.insert(0,{'type':'user','uuid':'tool','message':{'content':[{'type':'tool_result','content':'result'},{'type':'text','text':'tool context'}]}})
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in records))
        a.read_transcript();a.read_transcript()
        self.assertEqual(self.host.inputs,['real keyboard input'])

    async def test_oversized_tool_result_does_not_stall_later_desktop_input(self):
        a=shared.SharedClaudeAgent(self.host,self.cfg);await a.attach()
        records=[{'type':'user','message':{'content':[{'type':'tool_result','content':'x'*(2*1024*1024)}]}},
                 {'type':'user','uuid':'after-long','message':{'content':'desktop after long output'}}]
        self.transcript.write_text(''.join(json.dumps(r)+'\n' for r in records))
        a.read_transcript();a.read_transcript()
        self.assertEqual(self.host.inputs,['desktop after long output'])

    async def test_no_completion_before_matching_phone_input_and_reply(self):
        a=shared.SharedClaudeAgent(self.host,self.cfg);await a.attach()
        a.phone_turn=True; a.pending_text='specific instruction'
        self.transcript.write_text(json.dumps({'type':'user','uuid':'u','message':{'content':
            '<cross-session-message from-name="Agent-J-phone">specific instruction</cross-session-message>'}})+'\n')
        a.read_transcript(); self.assertFalse(a.done.is_set())
        with self.transcript.open('a') as f:
            f.write(json.dumps({'type':'assistant','uuid':'r','message':{'content':[{'type':'text','text':'finished'}]}})+'\n')
        a.read_transcript();self.assertFalse(a.done.is_set(), 'reply and idle registry cannot commit a turn')
        a.loop=asyncio.get_running_loop()
        await a.hook_event({'hook_event_name':'Stop','session_id':self.sid,'last_assistant_message':'finished'})
        await asyncio.sleep(0)
        self.assertTrue(a.done.is_set())
        self.assertEqual(self.host.inputs,[])
        self.assertEqual(self.host.phone_replies,['finished'])

class OpenCodeAttachment(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host=Host()
        mock_channel=patch.object(shared.SharedClaudeAgent,'prepare_channel',new=AsyncMock())
        mock_channel.start();self.addCleanup(mock_channel.stop);self.cfg={'kind':'opencode','dir':'/var/tmp/work','_workflow_ceo':True,
             'session_mode':'shared','high_risk_warnings':False,'shared_session_id':'sesOwner','shared_opencode_port':2345}
        self.a=shared.SharedOpenCodeAgent(self.host,self.cfg)
        self.a.client=Mock();self.a.client.request=AsyncMock(return_value=(200,{'id':'sesOwner','directory':'/var/tmp/work',
             'permission':[{'permission':'*','pattern':'*','action':'allow'}]}))

    async def test_attach_preserves_native_rules_no_patch_no_new_server(self):
        await self.a._attach()
        self.a.client.request.assert_awaited_once_with('GET','/session/sesOwner')
        self.assertEqual(self.a.rules,[])
        self.assertEqual(self.a.sid,'sesOwner')

    async def test_wrong_session_or_project_refused(self):
        for d in ({'id':'sesOther','directory':'/var/tmp/work'},{'id':'sesOwner','directory':'/var/tmp/other'}):
            self.a.client.request.return_value=(200,d)
            with self.assertRaises(shared.HTTPError):await self.a._attach()

    async def test_desktop_native_decision_releases_card_without_tamper(self):
        self.a.sid='sesOwner';f=asyncio.get_running_loop().create_future();self.a.pending['perOwner']=f
        self.a.on_event({'type':'permission.replied','properties':{'sessionID':'sesOwner','requestID':'perOwner','reply':'always'}})
        self.assertTrue(f.done());self.assertEqual(self.host.notices,[])

    async def test_other_session_reply_cannot_close_selected_session_card(self):
        self.a.sid='sesOwner';f=asyncio.get_running_loop().create_future();self.a.pending['perOwner']=f
        self.a.on_event({'type':'permission.replied','properties':{'sessionID':'sesOther','requestID':'perOwner','reply':'always'}})
        self.assertFalse(f.done())

    async def test_other_session_permission_is_never_answered(self):
        self.a.sid='sesOwner'
        self.a.on_event({'type':'permission.asked','properties':{'sessionID':'sesOther','id':'perOther'}})
        self.assertEqual(self.a.pending,{})

    async def test_reconnect_server_wide_permission_list_stays_session_scoped(self):
        self.a.sid='sesOwner'
        self.a._on_perm({'sessionID':'sesOther','id':'perOther'})
        self.assertEqual(self.a.pending,{})
        self.assertEqual(self.a.tasks,set())

    async def test_desktop_and_phone_user_parts_not_duplicated(self):
        self.a.sid='sesOwner';self.a.client.request.return_value=(200,[]);await self.a._prime()
        self.a.phone_messages.add('msgPhone')
        for mid,text in [('msgSynthetic','Base directory for this skill: test'),('msgReminder','<system-reminder>hook</system-reminder>'),('msgDesktop','from keyboard'),('msgPhone','from phone')]:
            self.a.on_event({'type':'message.updated','properties':{'info':{'sessionID':'sesOwner','id':mid,'role':'user'}}})
            ev={'type':'message.part.updated','properties':{'part':{'sessionID':'sesOwner','messageID':mid,'id':'prt'+mid,
               'type':'text','text':text}}}
            self.a.on_event(ev);self.a.on_event(ev)
        self.assertEqual(self.host.inputs,['from keyboard'])
        self.a.on_event({'type':'message.updated','properties':{'info':{'sessionID':'sesOwner','id':'msgAnswer','role':'assistant'}}})
        self.a.on_event({'type':'message.part.updated','properties':{'part':{'sessionID':'sesOwner','messageID':'msgAnswer',
            'id':'prtReply','type':'text','text':'answer','time':{'end':1}}}})
        self.assertEqual(self.host.replies,['answer'])

    async def test_stop_never_signals_owner_process(self):
        self.a.proc=shared._ExternalServer()
        with patch('os.killpg',side_effect=AssertionError('must never kill desktop')):
            await self.a.stop()
        self.assertIsNone(self.a.proc)

class OpenCodeOrdinaryStartup(unittest.IsolatedAsyncioTestCase):
    async def test_no_session_creates_native_without_patch_or_permission_override(self):
        cfg={'kind':'opencode','dir':'/var/tmp/work','_workflow_ceo':True,'session_mode':'shared','high_risk_warnings':False,'fence':False}
        a=shared.SharedOpenCodeAgent(Host(),cfg);a.client=Mock()
        a.client.request=AsyncMock(side_effect=[(200,{'id':'sesNew','directory':'/var/tmp/work'}),(200,[])])
        await a._prepare()
        self.assertEqual(a.client.request.call_args_list[0].args,('POST','/session',{'title':'Agent J shared'}))
        self.assertEqual(a.sid,'sesNew');self.assertEqual(a.rules,[])
        self.assertEqual(a.launch_argv(['/bin/native']),['/bin/native'])
    async def test_stale_explicit_owner_selector_never_spawns_another_server(self):
        cfg={'kind':'opencode','dir':'/var/tmp/work','_workflow_ceo':True,'session_mode':'shared','high_risk_warnings':False,'shared_session_id':'sesMissing'}
        a=shared.SharedOpenCodeAgent(Host(),cfg)
        with patch.object(shared.OpenCodeAgent,'_spawn',side_effect=AssertionError('must not fall back')):
            self.assertFalse(await a._spawn())

class SafetyDefaults(unittest.TestCase):
    def test_default_shared_keeps_warning_layer_on(self):
        self.assertEqual(preferences.defaults()['agent']['session_mode'],'shared')
        cfg={'kind':'claude','dir':'/var/tmp','_workflow_ceo':True,'session_mode':'shared'}
        self.assertIsInstance(shared.make_shared(Host(),cfg),shared.SharedClaudeAgent)
        cfg['high_risk_warnings']=False
        self.assertIsInstance(shared.make_shared(Host(),cfg),shared.SharedClaudeAgent)
        from agentj.shared_codex import SharedCodexAgent
        self.assertIsInstance(shared.make_shared(Host(),{**cfg,'kind':'codex'}),SharedCodexAgent)
    def test_transcript_credentials_scrubbed_before_history(self):
        planted='sk-'+('A'*30)
        self.assertNotIn(planted,shared.public_text('API key '+planted))
