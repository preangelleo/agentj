"""P589: idle ticks are local; automated wakes obey cost guards and report measured usage."""
import _hermetic  # noqa
import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_p156_heartbeat import Decide
from agentj import heartbeat_check as check, preferences


class Cost(Decide):
    # Inherit only fixture, not the old periodic-wake contract.
    def setUp(self):
        super().setUp()
        self.host.preferences = {'heartbeat': {'quiet_enabled': False}, 'updates': {'auto_install': False}}
        from agentj import heartbeat
        heartbeat.ensure_prompt('zh')
        self.h.started = self.h.last_activity = time.time()-7200
        self.probe = patch.object(check, 'collect', return_value=([], []))
        self.collect = self.probe.start()
        self.addCleanup(self.probe.stop)

    def test_no_work_no_model_and_restart(self):
        self.assertEqual(asyncio.run(self.h.tick())['why'], 'no_work')
        self.assertEqual(self.agent.got, [])
        from agentj.heartbeat import Heartbeat
        other = Heartbeat(self.host)
        other.started = other.last_activity = time.time()-7200
        self.assertEqual(asyncio.run(other.tick())['why'], 'no_work')
        self.assertEqual(self.agent.got, [])

    def test_work_reason_injected(self):
        self.collect.return_value = (['todo_due','background_reports'], [])
        self.assertTrue(asyncio.run(self.h.tick())['ok'])
        self.assertIn('todo_due, background_reports', self.agent.got[-1].text)

    def test_unknown_is_visible_not_a_success_or_model_wake(self):
        self.collect.return_value = ([], ['todo'])
        self.assertEqual(asyncio.run(self.h.tick())['why'], 'unknown')
        self.assertEqual(self.h.status()['precheck']['unknown'], ['todo'])
        self.assertFalse(self.agent.got)

    def test_context_never_pauses_the_beat(self):
        # #449 supersedes the P589 guard (>100k or expensive model → pause + owner /compact notice); see test_p449
        self.collect.return_value = (['todo_due'], [])
        self.host.agent_notice = Mock()
        self.host.meter_state = {'ctx': {'used': 120000}, 'model': 'gpt-6'}
        self.assertTrue(asyncio.run(self.h.tick())['ok'])
        self.host.agent_notice.assert_not_called()

    def test_daily_is_check_not_unconditional_wake(self):
        with patch.object(self.h, 'daily_is_due', return_value=True):
            self.assertEqual(asyncio.run(self.h.tick())['why'], 'no_work')
        self.assertFalse(self.agent.got)

    def test_prompt_change_deferred_in_quiet_but_urgent_work_allowed(self):
        self.h._save({'checked_sha': 'different', 'beats':{}})
        with patch.object(check, 'quiet', return_value=True), patch('agentj.heartbeat.read_prompt', return_value=('hello','abcdef12')):
            self.assertEqual(asyncio.run(self.h.tick())['why'], 'quiet')
            self.collect.return_value = (['todo_due'], [])
            self.assertTrue(asyncio.run(self.h.tick(time.time()+60))['ok'])

    def test_usage_exact_dedup_and_unknown(self):
        u = self.h.usage
        self.host._post = Mock()
        async def go():
            u.start(SimpleNamespace(heartbeat_key='k'))
            u.sample('step', 100, 20)
            u.sample('step', 120, 30)
            u.sample('step2', 50, 10)
            u.end()
            await u.task
        asyncio.run(go())
        self.assertEqual(u.view()['total_tokens'], 210)
        self.assertEqual(json.loads(u.path.read_text())['input_tokens'], 170)
        self.assertTrue(self.host._meter_msg()['heartbeat_usage']['available'])
        async def missing():
            u.start(SimpleNamespace(heartbeat_key='k'))
            u.end()
            await u.task
        asyncio.run(missing())
        self.assertFalse(u.view()['complete'])
        self.assertEqual(u.view()['turns'], 2)
        u.start(None)
        u.sample('owner', 900, 900)
        u.end()
        self.assertEqual(u.view()['total_tokens'], 210)

    def test_native_codex_two_heartbeats_exclude_owner_and_prior_history(self):
        from agentj.shared_codex import SharedCodexAgent
        a = SharedCodexAgent(self.host, {'dir':self.tmp.name,'_workflow_ceo':True})
        a.host, a.tid, a.usage = self.host, 'thread', None
        a.touch = Mock()
        a.research, a.persist = True, False
        a.cur_model = Mock(return_value='gpt-6')
        self.host.agent = a
        u = self.h.usage
        def note(inp, out, last_in, last_out):
            a._on_note('thread/tokenUsage/updated', {'threadId':'thread', 'tokenUsage':{
                'total':{'inputTokens':inp,'outputTokens':out},
                'last':{'inputTokens':last_in,'outputTokens':last_out}}})
        async def run():
            u.start(SimpleNamespace(heartbeat_key='k'))
            a._on_note('turn/started', {'threadId':'thread'})
            note(1100, 120, 100, 20)
            note(1150, 125, 50, 5)
            u.end()
            await u.task
            u.start(None)
            a._on_note('turn/started', {'threadId':'thread'})
            note(2000, 200, 850, 75)
            u.end()
            u.start(SimpleNamespace(heartbeat_key='k'))
            a._on_note('turn/started', {'threadId':'thread'})
            note(2100, 210, 100, 10)
            u.end()
            await u.task
        asyncio.run(run())
        self.assertEqual(u.view()['input_tokens'], 250)
        self.assertEqual(u.view()['output_tokens'], 35)
        self.assertEqual(u.view()['turns'], 2)

    def test_native_claude_includes_cache_input(self):
        from agentj.agent import ClaudeAgent
        a = ClaudeAgent(self.host, {'dir':self.tmp.name,'_workflow_ceo':True})
        a.host, a.touch, a.turn_done = self.host, Mock(), None
        self.host.agent = a
        u = self.h.usage
        async def run():
            u.start(SimpleNamespace(heartbeat_key='k'))
            a.on_event({'type':'result','subtype':'success', 'usage':{
                'input_tokens':10,'cache_read_input_tokens':100,'cache_creation_input_tokens':30,'output_tokens':20}})
            u.end()
            await u.task
        asyncio.run(run())
        self.assertEqual(u.view()['input_tokens'], 140)
        self.assertEqual(u.view()['total_tokens'], 160)

    def test_shared_claude_transcript_anchor_and_history_replay(self):
        from agentj.shared import SharedClaudeAgent
        a = SharedClaudeAgent.__new__(SharedClaudeAgent)
        a.host, a.session, a.offset, a.seen = self.host, None, 0, set()
        a.transcript = self.st.root / 'transcript.jsonl'
        a.discard_line, a.ended_at = False, None
        a._identity_seen, a._turn_output = Mock(), Mock()
        self.host.agent = a
        u = self.h.usage
        def row(uuid, mid, inp):
            return json.dumps({'uuid':uuid, 'type':'assistant', 'message':{'id':mid,'content':[],
                'usage':{'input_tokens':inp,'cache_read_input_tokens':50,'output_tokens':10}}})+'\n'
        a.transcript.write_text(row('old','historical',900))
        a.phone_anchored = False
        a.read_transcript()
        async def run():
            u.start(SimpleNamespace(heartbeat_key='k'))
            a.phone_anchored = True
            with a.transcript.open('a') as f:
                f.write(row('replayed','historical',900))
                f.write(row('new','current',100))
            a.read_transcript()
            u.end()
            await u.task
        asyncio.run(run())
        self.assertEqual(u.view()['input_tokens'], 150)
        self.assertEqual(u.view()['total_tokens'], 160)

    def test_native_opencode_versions_cache_reasoning_and_replay(self):
        from agentj.shared import SharedOpenCodeAgent
        from agentj.shared_opencode2 import SharedOpenCodeV2Agent
        async def run(cls, v2):
            a = cls(self.host, {'dir':self.tmp.name,'_workflow_ceo':True})
            a.host, a.sid, a._kids, a.touch = self.host, 's', {}, Mock()
            a.message_roles = {}
            self.host.agent = a
            u = self.h.usage
            tokens = {'input':10,'output':20,'reasoning':3,'cache':{'read':100,'write':5}}
            def event(mid):
                if v2:
                    a.on_event({'type':'session.step.ended','data':{'sessionID':'s','assistantMessageID':mid,'ordinal':1,'tokens':tokens}})
                else:
                    a.on_event({'type':'message.updated','properties':{'info':{'sessionID':'s','id':mid,'role':'assistant','tokens':tokens}}})
            event('old')
            before = u.data['total_tokens']
            u.start(SimpleNamespace(heartbeat_key='k'))
            event('old')
            event('new-v2' if v2 else 'new-v1')
            event('new-v2' if v2 else 'new-v1')
            u.end()
            await u.task
            self.assertEqual(u.data['total_tokens']-before, 138)
        asyncio.run(run(SharedOpenCodeAgent, False))
        asyncio.run(run(SharedOpenCodeV2Agent, True))

    def test_v2_step_preserves_usage_phase_and_heartbeat_sampling(self):
        from agentj.agent_opencode2 import OpenCodeV2Agent
        a=OpenCodeV2Agent(self.host,{'dir':self.tmp.name,'model':'fixture/model'})
        a.sid='synthetic';self.host.agent=a
        self.host.agent_phase=Mock();self.host.turn_use=Mock()
        async def run():
            usage=self.h.usage
            usage.start(SimpleNamespace(heartbeat_key='fixture'))
            a.on_event({'type':'session.step.ended','data':{'sessionID':'synthetic',
                'assistantMessageID':'step','ordinal':1,'cost':0.0002,
                'tokens':{'input':10,'output':20,'reasoning':3,'cache':{'read':100,'write':5}}}})
            usage.end();await usage.task
        asyncio.run(run())
        self.host.agent_phase.assert_called_with('gen',None)
        self.assertEqual(self.host.turn_use.call_args.args[0],'step:step:1')
        self.assertEqual(self.host.turn_use.call_args.args[2],0.0002)
        self.assertEqual(self.h.usage.view()['input_tokens'],115)
        self.assertEqual(self.h.usage.view()['output_tokens'],23)
        self.assertEqual(self.h.usage.view()['total_tokens'],138)

# Avoid inheriting superseded unconditional-timer tests; retain their fixture only.
for name in dir(Decide):
    if name.startswith('test_') and name not in Cost.__dict__:
        setattr(Cost, name, None)
del Decide


class Signals(unittest.TestCase):
    def test_quiet_crosses_midnight_and_can_disable(self):
        def at(h):
            return time.mktime((2026,10,30,h,0,0,0,0,-1))
        self.assertTrue(check.quiet({}, at(23)))
        self.assertTrue(check.quiet({}, at(6)))
        self.assertFalse(check.quiet({}, at(7)))
        self.assertFalse(check.quiet({'heartbeat':{'quiet_enabled':False}}, at(1)))

    def test_sources_and_read_failures(self):
        h = SimpleNamespace(st=SimpleNamespace(root='/tmp'), agent=SimpleNamespace(kind='claude'), preferences={},
                            meter_state={}, background_done=SimpleNamespace(pending={'a':{'exit_code':1}}))
        with patch('agentj.jcards.find', return_value={}), patch('agentj.jcards_todo.listing', return_value={'ok':True,'items':[{}]}), \
             patch('agentj.jcards_cal.listing', return_value={'ok':False}), patch('agentj.avatar_log.rows', return_value=[{'needs_review':True}]), \
             patch('agentj.goal.heartbeat_line', return_value=None), patch('agentj.quota.reminders_on', return_value=False), \
             patch('agentj.auth_rescue.heartbeat_line', return_value=None), patch('subprocess.run', return_value=SimpleNamespace(returncode=0,stdout='unit.service failed')):
            reasons, unknown = check.collect(h, time.time())
        self.assertEqual(set(reasons), {'todo_due','background_reports','failed_work','avatar_review','failed_units'})
        self.assertEqual(unknown, ['calendar'])

    def test_schema(self):
        prefs = preferences.defaults()['heartbeat']
        self.assertEqual(prefs['quiet_start'], '23:00')
        self.assertEqual(prefs['quiet_end'], '07:00')
        with self.assertRaises(preferences.ConfigError):
            preferences.validate({'heartbeat':{'quiet_start':'25:00'}})
