import asyncio
import unittest
from types import SimpleNamespace
from agentj.agent import Agent, claude_output
from agentj.agent_codex import codex_output
from agentj.agent_opencode import opencode_output
from agentj.agent_opencode2 import opencode2_output

class Classifiers(unittest.TestCase):
    def test_claude_reasoning_synthetic_and_text(self):
        self.assertFalse(claude_output({'type':'assistant','message':{'model':'<synthetic>','content':[{'type':'text','text':'local'}]}}))
        self.assertFalse(claude_output({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'private'}]}}))
        self.assertTrue(claude_output({'type':'assistant','message':{'content':[{'type':'text','text':'hello'}]}}))
    def test_codex_reasoning_not_output(self):
        self.assertFalse(codex_output('item/started', {'item':{'type':'reasoning'}}))
        self.assertFalse(codex_output('item/started', {'item':{'type':'userMessage'}}))
        self.assertTrue(codex_output('item/started', {'item':{'type':'commandExecution'}}))
    def test_opencode_sessions_and_synthetic(self):
        p = {'part':{'sessionID':'ours','type':'text','time':{'start':1}}}
        self.assertTrue(opencode_output('message.part.updated', p, 'ours'))
        self.assertFalse(opencode_output('message.part.updated', p, 'foreign'))
        p['part']['synthetic'] = True
        self.assertFalse(opencode_output('message.part.updated', p, 'ours'))
        self.assertFalse(opencode2_output('session.reasoning.delta'))
        self.assertTrue(opencode2_output('session.tool.started'))
        self.assertTrue(opencode2_output('form.created'))
    def test_first_output_once_workflow_excluded(self):
        calls = []
        a = Agent.__new__(Agent)
        a.host = SimpleNamespace(agent_first_out=lambda: calls.append(1))
        a.cfg = {}
        a.first_output(); a.first_output()
        self.assertEqual(calls, [1])
        a.out_seen = False
        a.cfg['_workflow_ceo'] = True
        a.first_output()
        self.assertEqual(calls, [1])
