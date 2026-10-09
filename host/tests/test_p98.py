"""P98: shared notice identity is native, opaque and optional; conflicts remain actionable."""
import _hermetic
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, Mock
import asyncio
from agentj.shared import SharedOpenCodeAgent
from agentj.serve import Host

class SharedFollow(unittest.TestCase):
    def host(self, kind, **kw):
        h=Host.__new__(Host)
        h.agent=SimpleNamespace(kind=kind,cfg={"session_mode":"shared","shared_session_id":"configured-pin"},**kw)
        h.meter_state={"shared_status":"desktop_writer","shared_writer":{"en":"Quit the App and resend"}}
        return h

    def test_native_identity_all_adapters_no_raw_session_or_stale_pin(self):
        for kind,kw in [("claude",{"session":{"sessionId":"actual-session"}}),("codex",{}),("opencode",{"sid":"actual-session"})]:
            h=self.host(kind,**kw);m=h._meter_msg()
            self.assertEqual(m['shared_follow']['agent'],kind)
            self.assertRegex(m['shared_follow']['id'],r'^[a-f0-9]{32}$')
            self.assertNotIn('actual-session',str(m));self.assertNotIn('configured-pin',str(m))
            self.assertEqual(m['shared_status'],'desktop_writer');self.assertIn('resend',m['shared_writer']['en'])
            old=m['shared_follow']
            if kind=='claude': h.agent.session['sessionId']='new'
            elif kind=='opencode': h.agent.sid='new'
            else: h.agent.cfg['shared_session_id']='new'
            self.assertNotEqual(h._meter_msg()['shared_follow'],old)

    def test_unattached_independent_and_no_agent_clear_metadata(self):
        for kind in ('claude','opencode'):
            h=self.host(kind);self.assertIsNone(h._meter_msg().get('shared_follow'))
        h=self.host('codex');h.agent.cfg['session_mode']='independent';self.assertIsNone(h._meter_msg().get('shared_follow'))
        h.agent=None;self.assertIsNone(h._meter_msg().get('shared_follow'))

    def test_session_only_change_triggers_meter_without_quota_change(self):
        h=self.host('claude',session={'sessionId':'one'})
        with patch('agentj.serve.asyncio.get_running_loop',side_effect=RuntimeError):
            h.meter_update();old=h._last_shared_follow
            h.agent.session['sessionId']='two';h.meter_update()
        self.assertNotEqual(h._last_shared_follow,old)
        self.assertEqual(h._meter_msg()['shared_follow'],h._last_shared_follow)

    def test_attached_opencode_announces_identity_without_turn_or_quota(self):
        a=SharedOpenCodeAgent.__new__(SharedOpenCodeAgent)
        a.sid='ses-fixture';a.client=SimpleNamespace(request=AsyncMock(return_value=(200,[])));a.meter=Mock()
        asyncio.run(a._prime())
        a.meter.assert_called_once_with()
