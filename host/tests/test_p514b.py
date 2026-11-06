"""#514b: same-boot upgrades must recover the served Claude session and report real readiness.
Local registered sleeping child fixture; no Claude login/model/service invocation.
"""
import _hermetic  # noqa: F401
import io
import json
from unittest.mock import patch

from test_p418_shared import ClaudeFixture, Host, PIN
from agentj import doctor, shared, update


class UpgradeResume(ClaudeFixture):
    async def test_same_boot_upgrade_resumes_the_previously_served_pin(self):
        self.transcript(PIN, mtime=self.boot + 60)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        with patch.object(update, 'upgrade_due', return_value=True):
            self.assertTrue(await self.attach(a))
        try:
            self.assertEqual(self.calls[0][0][-2:], ['--resume', PIN])
            self.assertEqual(a.session['sessionId'], PIN)
            self.assertEqual(a.status, 'idle')
            self.assertTrue(any('升级' in s for s in a.host.notices))
        finally:
            await self.drop(a)

    async def test_upgrade_unknown_pin_starts_fresh_without_resuming_it(self):
        self.transcript(PIN, mtime=self.boot + 60)
        a = shared.SharedClaudeAgent(Host(last=None), dict(self.cfg))
        with patch.object(update, 'upgrade_due', return_value=True):
            self.assertTrue(await self.attach(a))
        try:
            self.assertNotIn('--resume', self.calls[0][0])
        finally:
            await self.drop(a)

    async def test_explicit_reconnect_resumes_after_service_restart(self):
        self.transcript(PIN, mtime=self.boot + 60)
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        with patch.object(shared.asyncio, 'create_subprocess_exec', new=self.fake_exec), \
                patch.object(shared, 'boot_time', return_value=self.boot), \
                patch.object(update, 'upgrade_due', return_value=False):
            result = await a.reconnect()
        try:
            self.assertTrue(result['ok'], result)
            self.assertEqual(self.calls[0][0][-2:], ['--resume', PIN])
        finally:
            await self.drop(a)

    async def test_receipt_consuming_marker_does_not_prevent_automatic_resume(self):
        import time
        host = Host(last=PIN)
        host.st.root = self.root
        marker = self.root / update.UPGRADED
        marker.write_text(json.dumps({'to': update.__version__, 'at': time.time()}))
        self.transcript(PIN, mtime=self.boot + 60)
        a = shared.SharedClaudeAgent(host, dict(self.cfg))
        marker.unlink()
        with patch.object(update, 'upgrade_due', return_value=False):
            self.assertTrue(await self.attach(a))
        await self.drop(a)

    async def test_explicit_reconnect_unknown_pin_starts_managed_without_resume(self):
        self.transcript(PIN, mtime=self.boot - 60)
        a = shared.SharedClaudeAgent(Host(last=None), dict(self.cfg))
        await self.assert_fresh_repair(a)

    async def test_explicit_reconnect_missing_transcript_starts_managed(self):
        a = shared.SharedClaudeAgent(Host(last=PIN), dict(self.cfg))
        await self.assert_fresh_repair(a)

    async def test_explicit_reconnect_no_pin_starts_managed(self):
        a = shared.SharedClaudeAgent(Host(last=None), {**self.cfg, 'shared_session_id': ''})
        await self.assert_fresh_repair(a)

    async def assert_fresh_repair(self, a):
        with patch.object(shared.asyncio, 'create_subprocess_exec', new=self.fake_exec):
            result = await a.reconnect()
        try:
            self.assertTrue(result['ok'], result)
            self.assertNotIn('--resume', self.calls[0][0])
            self.assertEqual(self.calls[0][1]['cwd'], self.cfg['dir'])
        finally:
            await self.drop(a)


    async def test_failed_automatic_start_is_bounded_and_drops_child(self):
        from unittest.mock import AsyncMock
        a = shared.SharedClaudeAgent(Host(), {**self.cfg, 'shared_session_id': ''})
        with patch.object(a, 'ordinary_start', new=AsyncMock(side_effect=ValueError('unregistered'))) as start:
            self.assertFalse(await a.attach())
            self.assertFalse(await a.attach())
            self.assertEqual(start.await_count, 1)
        self.assertIsNone(a.native)

    async def test_concurrent_reconnects_create_only_one_registered_child(self):
        import asyncio
        a = shared.SharedClaudeAgent(Host(), {**self.cfg, 'shared_session_id': ''})
        with patch.object(shared.asyncio, 'create_subprocess_exec', new=self.fake_exec):
            first, second = await asyncio.gather(a.reconnect(), a.reconnect())
        try:
            self.assertTrue(first['ok'] and second['ok'])
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(second['changed'])
        finally:
            await self.drop(a)



class SessionHealth(__import__('unittest').TestCase):
    def row(self, status='down'):
        from unittest.mock import Mock
        st = Mock()
        runtime = {'relay_up': True, 'relay_state': 'up', 'agent': 'claude', 'agent_status': status}
        with patch.object(doctor, '_fenced', return_value=False), \
                patch.object(doctor.names, 'ctl_call', return_value=runtime):
            return doctor.check_serve(st)

    def test_detached_shared_new_session_button_uses_existing_reconnect(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        from agentj import reset
        host = Mock()
        host.agent.kind = 'claude'
        host.agent.cfg = {'session_mode': 'shared'}
        host.agent.session = None
        host.agent_reconnect = AsyncMock(return_value={'ok': True})
        self.assertTrue(asyncio.run(reset.new_session(host))['ok'])
        host.agent_reconnect.assert_awaited_once_with()

    def test_connected_relay_detached_claude_is_not_green(self):
        row = self.row()
        self.assertEqual(row['status'], 'warn', row)
        self.assertIn('Claude', row['summary'])
        for lang in ('zh', 'en'):
            text = update.completion_text({'to': '0.26.12'}, update.doctor_counts([row]), lang, [row])
            self.assertNotIn('主机本次自检通过', text)
            self.assertNotIn('host self-check passed', text)
            self.assertIn('Claude', text)

    def test_positive_control_attached_idle_is_green(self):
        self.assertEqual(self.row('idle')['status'], 'ok')

    def test_cli_upgrade_result_never_promises_attached_session(self):
        text = update.result_block({'result': 'ok', 'reason': 'upgraded', 'from': '0.26.10a4', 'to': '0.26.12',
                                    'service': 'restart_scheduled'})
        self.assertNotIn('一切正常', text)
        self.assertNotIn('everything is working', text)
        self.assertIn('after restart', text)

    def test_unselected_claude_session_is_not_green(self):
        self.assertTrue(self.row('none')['session_disconnected'])

    def test_durable_phone_receipt_keeps_disconnected_evidence(self):
        from agentj import phone_update
        row = self.row()
        for lang in ('zh', 'en'):
            text = phone_update.text({'reason':'upgraded', 'to':'0.26.12', 'counts':(0,1,0), 'checks':[row]}, lang)
            self.assertNotIn('host self-check passed', text)
            self.assertNotIn('主机本次自检通过', text)
            self.assertIn('Claude', text)
