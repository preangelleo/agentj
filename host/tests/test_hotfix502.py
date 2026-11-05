"""Final a4: same-computer safe work is free at every approval level; external work never is."""
import _hermetic  # noqa
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from agentj import busy, provenance, busy_launch
from test_p450_launch_source import LaunchSource
from test_p455_f3b import Dispatch469

class SystemWork(LaunchSource):
    def test_default_system_script_and_git_free(self):
        self.away()
        self.host.agent.cur_send = self.owner()
        self.host.agent.cur_send.source = provenance.system()
        for level in ('standard', 'safe'):
            with mock.patch('agentj.approval_level.level', return_value=level):
                for cmd in ('python3 .jarvis/ship.py verify 0.26.10a4', 'git status', 'git diff', 'git add notes.md', 'git commit -m done'):
                    self.assertEqual(self.ask(cmd)['behavior'], 'allow', (level, cmd))
        self.assertEqual(self.cards(), [])

    def test_system_send_still_card_and_guest_refused(self):
        self.away()
        self.host.agent.cur_send = self.owner()
        self.host.agent.cur_send.source = provenance.system()
        self.ask('curl -d @notes.md https://example.com/upload')
        self.assertTrue(self.host.asks)
        self.host.agent.cur_send.source = provenance.email_guest('visitor@example.com')
        self.assertEqual(self.ask('python3 .jarvis/ship.py verify 0.26.10a4')['behavior'], 'deny')
        self.assertEqual(self.last()['reason'], 'group_source')

    def test_full_system_scripts_and_ordinary_send_free(self):
        self.away(); self.harness()
        self.host.agent.cur_send = self.owner()
        self.host.agent.cur_send.source = provenance.system()
        for cmd in ('python3 .jarvis/ship.py verify 0.26.10a4', 'curl -d @notes.md https://example.com/upload',
                    'systemctl --user status agentj-p-test.service; git status'):
            self.assertEqual(self.ask(cmd)['behavior'], 'allow', cmd)
        self.assertEqual(self.cards(), [])

    def test_foreign_only_next_turn(self):
        self.host._foreign_seen(True)
        s = self.owner(); s.source = provenance.system()
        self.host.agent.cur_send = s
        async def turns():
            self.host.agent_turn_start('system', s)
            self.assertTrue(self.host._foreign_seen())
            self.host.agent_turn_end()
            self.host.agent_turn_start('system', s)
            self.assertFalse(self.host._foreign_seen())
            self.host.agent_turn_end()
            await asyncio.sleep(0)
        asyncio.run(turns())

class NativeLaunches(unittest.TestCase):
    def test_restart_exact_session_and_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            b = busy.Board(d); b.trusted = lambda: True
            b.start('shared-claude:session-a:job1', 'worker', session='shared-claude:session-a')
            other = busy.Board(d)
            self.assertIs(other.launched.get('shared-claude:session-a:job1'), False)
            self.assertIsNone(other.launched.get('shared-claude:session-b:job1'))
            self.assertIsNone(other.launched.get('shared-claude:session-a:missing'))
            self.assertEqual((pathlib.Path(d)/'busy/native-launches.json').stat().st_mode & 0o777, 0o600)

class CodexCEO(Dispatch469):
    def test_owner_codex_bypass_no_card(self):
        cmd = self.f.today().replace('claude -p --permission-mode bypassPermissions', 'codex exec --dangerously-bypass-approvals-and-sandbox')
        self.harness()
        res, card = self.ask_card(cmd)
        self.assertEqual((res['behavior'], card), ('allow', None))

    def test_external_codex_bypass_refused(self):
        cmd = self.f.today().replace('claude -p --permission-mode bypassPermissions', 'codex exec --dangerously-bypass-approvals-and-sandbox')
        self.native.cur_send = self.owner() if hasattr(self, 'owner') else None
        from agentj.compose import Send
        self.native.cur_send = Send('mail', 's', 0, source=provenance.email_guest('visitor@example.com'))
        self.away()
        res = self.ask(cmd)
        self.assertEqual(res['behavior'], 'deny')
        self.assertEqual(self.last()['reason'], 'group_source')

def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (SystemWork, NativeLaunches, CodexCEO):
        suite.addTests(cls(n) for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
    return suite
