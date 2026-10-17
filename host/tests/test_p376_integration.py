"""Release seams: identity remains inline; webhook grants are bounded and belong to one order."""
import _hermetic  # noqa: F401
import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import Mock
from agentj import main_identity, shared, hook_service
from agentj.core import orders, toolbox

BRIEF = {"goal": "integration fixture", "scope": {"write": ["*.txt"]}, "verify": ["true"], "timebox": 5, "report": "head"}

class Integration(unittest.TestCase):
    def test_shared_claude_complete_first_hook_retains_300_chars(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            host = Mock(); host.st.root = root; host.stopped.return_value = False
            host.st.write_private.side_effect = lambda p, b: pathlib.Path(p).write_bytes(b)
            cfg = {"kind": "claude", "dir": td, "language": "zh", "session_mode": "shared"}
            agent = shared.SharedClaudeAgent(host, cfg)
            agent.session = {"sessionId": "owner", "pid": 1}
            agent.phone_turn, agent.pending_text = True, "hi"
            answer = asyncio.run(agent.hook_event({"session_id": "owner", "hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
            text = answer['hookSpecificOutput']['additionalContext']
            self.assertEqual(agent.identity_marker[3], 'hook.additionalContext')
            self.assertLessEqual(len(text), agent.HOOK_MAX - 300)
            self.assertLessEqual(len(main_identity.prompt(cfg, harness='claude')), agent.INLINE_MAX - 300)
            for rule in ('documentation/ROLES.md', 'agentj secret request', 'agentj doctor --reconnect', 'reply:选项'):
                self.assertIn(rule, text)

    def test_actual_order_sink_uses_host_state_and_grants_do_not_leak(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); project = root / 'project'; project.mkdir()
            state = root / 'isolated-state'
            grant = {'hooks': {'max': 2, 'life': ['once', 'ttl'], 'ttl_max': 600}}
            kw = dict(project=str(project), prompt_text='fixture', brief=BRIEF, title='fixture', st_root=state)
            granted = orders.create(**kw, grants=grant); plain = orders.create(**kw)
            sink = hook_service.OrderSink(state)
            self.assertTrue(sink.live(granted.id))
            self.assertEqual(sink.grant(granted.id), grant['hooks'])
            self.assertIsNone(sink.grant(plain.id))
            grant['hooks']['max'] = 9
            self.assertEqual(sink.grant(granted.id)['max'], 2)
            self.assertTrue(sink.deliver(granted.id, 'external fixture data', 'fixture-hook', 'delivery'))
            self.assertIn('external fixture data', '\n'.join(text for _, text in granted.inbox_all()))

    def test_invalid_and_resident_grants_are_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            for h in ({'max': 0, 'life': ['once'], 'ttl_max': 10},
                      {'max': 1, 'life': ['resident'], 'ttl_max': 10},
                      {'max': 1, 'life': ['once'], 'ttl_max': 86401},
                      {'max': True, 'life': ['ttl'], 'ttl_max': 10},
                      {'max': 1, 'life': ['once'], 'ttl_max': 10, 'owner': True}):
                with self.subTest(grant=h), self.assertRaises(orders.OrderError):
                    orders.create(project=td, prompt_text='fixture', brief=BRIEF, title='fixture',
                                  st_root=pathlib.Path(td) / 'state', grants={'hooks': h})
            self.assertFalse((pathlib.Path(td) / 'state').exists())

    def test_toolbox_retains_nonsecret_order_id_without_model_key(self):
        env = toolbox.environment({'AGENTJ_ORDER_ID': 'o-fixture', 'EXAMPLE_API_KEY': 'fixture', 'HOME': '/tmp/fixture'})
        self.assertEqual(env['AGENTJ_ORDER_ID'], 'o-fixture')
        self.assertNotIn('EXAMPLE_API_KEY', env)
