"""0313: context thresholds use a coherent current window, never the absolute cap."""
import _hermetic  # noqa
import unittest
from agentj import context_snapshot, claude_statusline, heartbeat, compactprep
from types import SimpleNamespace

class ContextSnapshot(unittest.TestCase):
    def test_true_window_boundaries(self):
        for used in (100000, 150000, 400000, 499999):
            self.assertLess(context_snapshot.percent({'used': used, 'max': 1000000}), 50)
        self.assertEqual(context_snapshot.percent({'used': 500000, 'max': 1000000}), 50)
        self.assertEqual(context_snapshot.percent({'used': 750000, 'max': 1000000}), 75)
        self.assertEqual(context_snapshot.percent({'used': 100000, 'max': 200000}), 50)
    def test_pct_only_is_displayable_but_unknown_for_thresholds(self):
        self.assertEqual(context_snapshot.display_percent({'pct': 80}), 80)
        self.assertIsNone(context_snapshot.percent({'pct': 80}))

    def test_unknown_and_contradictions(self):
        for ctx in (None, {'used': 100000}, {'used': True, 'max': 200000},
                    {'used': 900000, 'max': 1000000, 'pct': 15},
                    {'used': 200001, 'max': 200000}, {'used': float('nan'), 'max': 200000}):
            self.assertIsNone(context_snapshot.percent(ctx), ctx)
    def test_half_and_three_quarters_same_snapshot(self):
        a = SimpleNamespace(kind='claude', cfg={}, session={'sessionId': 'now'})
        h = SimpleNamespace(agent=a, meter_state={'ctx': {'used': 499999, 'max': 1000000}}, lang='zh')
        beat = object.__new__(heartbeat.Heartbeat)
        beat.host = h
        self.assertIsNone(beat.context_note(0, {}))
        h.meter_state['ctx']['used'] = 500000
        self.assertIn('上下文已用到一半', beat.context_note(0, {}))
        h.meter_state['ctx']['used'] = 750000
        self.assertIn('上下文已用到四分之三', beat.context_note(0, {}))

    def test_binding_stale_and_notices(self):
        a = SimpleNamespace(kind='claude', cfg={'model': 'opus'}, session={'sessionId': 'now'})
        h = SimpleNamespace(agent=a, meter_state={'ctx': {'used': 500000, 'max': 1000000,
                           'session_id': 'previous', 'model_id': 'opus'}})
        beat = object.__new__(heartbeat.Heartbeat)
        beat.host = h
        self.assertIsNone(beat.context_note(0, {}))
        h.meter_state['ctx']['session_id'] = 'now'
        h.meter_state['ctx']['model_id'] = 'older'
        self.assertIsNone(beat.context_note(0, {}))
        h.meter_state['ctx']['model_id'] = 'opus'
        h.meter_state['ctx']['at'] = 1
        self.assertIsNone(beat.context_note(0, {}))
        h.meter_state['ctx'].pop('at')
        self.assertEqual(context_snapshot.percent(context_snapshot.current(h)), 50)
        h.meter_state['ctx']['used'] = 499999
        self.assertIsNone(beat.context_note(0, {}))
        a.host = h
        self.assertEqual(compactprep._ctx(a), (499999, 1000000))
        h.meter_state['ctx']['session_id'] = 'previous'
        self.assertIsNone(compactprep._ctx(a))

    def test_native_producers_reach_phone_and_notices(self):
        from agentj import acp, agent_codex, serve
        for kind, cls, sid_attr, event in (
            ('kimi', acp.AcpAgent, 'sid', ('session/update', {
                'sessionId': 'native-a', 'update': {'sessionUpdate': 'usage_update', 'used': 750, 'size': 1000}})),
            ('codex', agent_codex.CodexAgent, 'tid', ('thread/tokenUsage/updated', {
                'threadId': 'native-a', 'tokenUsage': {'last': {'totalTokens': 750}, 'modelContextWindow': 1000}})),
        ):
            with self.subTest(kind=kind):
                host = SimpleNamespace(meter_state={}, meter_task=None, meter_at=0, agent=None, heartbeat=None)
                host._shared_follow = lambda: None
                host._last_shared_follow = None
                host.meter_update = lambda **kw: serve.Host.meter_update(host, **kw)
                host._meter_msg = lambda: serve.Host._meter_msg(host)
                agent = object.__new__(cls)
                agent.host, agent.kind, agent.cfg, agent.session = host, kind, {'model': 'native-model'}, {}
                agent.persist, agent.research = True, False
                setattr(agent, sid_attr, 'native-a')
                host.agent = agent
                agent.meter = lambda **kw: host.meter_update(**kw)
                agent.cur_model = lambda: 'native-model'
                if kind == 'kimi':
                    agent.loading, agent.usage = False, None
                    agent.touch = lambda: None
                else:
                    agent.usage, agent.context_metadata, agent.use_base = None, None, None
                    agent.proc = None
                    agent.touch = lambda: None
                    agent.turn_id = None
                    agent.use_put = lambda *args: None
                agent._on_note(*event)
                self.assertEqual(host.meter_state['ctx']['session_id'], 'native-a')
                self.assertEqual(host._meter_msg()['ctx']['used'], 750)
                self.assertEqual(context_snapshot.percent(context_snapshot.current(host)), 75)
                beat = object.__new__(heartbeat.Heartbeat)
                beat.host = host
                self.assertIn('四分之三', beat.context_note(0, {}))
                agent.host = host
                self.assertEqual(compactprep._ctx(agent), (750, 1000))
                setattr(agent, sid_attr, 'native-b')
                self.assertIsNone(host._meter_msg()['ctx'])

    def test_statusline_pct_only_reaches_phone(self):
        from agentj import serve
        blob = {'session_id': 'native-a', 'model': {'id': 'opus'},
                'context_window': {'used_percentage': 80}}
        ctx = claude_statusline.measured(blob)['ctx']
        agent = SimpleNamespace(kind='claude', cfg={}, session={'sessionId': 'native-a'})
        host = SimpleNamespace(agent=agent, meter_state={}, meter_task=None, meter_at=0, heartbeat=None)
        host._shared_follow = lambda: None
        host._last_shared_follow = None
        serve.Host.meter_update(host, ctx={**ctx, 'session_id': 'native-a'})
        self.assertEqual(serve.Host._meter_msg(host)['ctx']['pct'], 80)
        self.assertIsNone(context_snapshot.percent(serve.Host._meter_msg(host)['ctx']))

    def test_pct_only_phone_but_never_threshold(self):
        from agentj import serve
        agent = SimpleNamespace(kind='claude', cfg={}, session={'sessionId': 'native-a'})
        host = SimpleNamespace(agent=agent, meter_state={}, meter_task=None, meter_at=0, heartbeat=None)
        host._shared_follow = lambda: None
        host._last_shared_follow = None
        agent.host = host
        serve.Host.meter_update(host, ctx={'pct': 80, 'session_id': 'native-a'})
        self.assertEqual(serve.Host._meter_msg(host)['ctx']['pct'], 80)
        self.assertIsNone(context_snapshot.percent(context_snapshot.current(host)))
        beat = object.__new__(heartbeat.Heartbeat)
        beat.host = host
        self.assertIsNone(beat.context_note(0, {}))
        agent.session['sessionId'] = 'native-b'
        self.assertIsNone(serve.Host._meter_msg(host)['ctx'])
        self.assertIsNone(compactprep._ctx(agent))

    def test_shared_last_request_and_inconsistent_percent(self):
        blob = {'session_id': 'test', 'model': {'id': 'claude-opus-5-5'}, 'context_window': {
            'context_window_size': 1000000, 'used_percentage': 15,
            'current_usage': {'input_tokens': 50000, 'output_tokens': 10000,
                              'cache_creation_input_tokens': 30000, 'cache_read_input_tokens': 60000}}}
        self.assertEqual(claude_statusline.measured(blob)['ctx']['pct'], 15)
        blob['context_window']['current_usage']['cache_read_input_tokens'] = 850000
        self.assertEqual(claude_statusline.measured(blob)['ctx']['pct'], 15)
        self.assertIsNone(context_snapshot.percent(claude_statusline.measured(blob)['ctx']))
