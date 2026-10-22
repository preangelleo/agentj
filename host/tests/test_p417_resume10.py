"""P417 resume: current-harness reminders, retired plan, named errors, single Esc."""
import _hermetic  # noqa: F401
import asyncio
import io
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from agentj import bailian, keys, quota, shared
from agentj.agent_opencode import OpenCodeAgent


class Resume10(unittest.TestCase):
    def test_only_active_harness_is_marked_for_heartbeat_reminders(self):
        rep = {"harnesses": [{"harness": "claude"}, {"harness": "opencode"}]}
        with patch.object(quota, 'report', return_value=rep), patch.object(quota, 'due', return_value=[]) as due:
            self.assertEqual(quota.heartbeat_lines('/unused', {}, active_harness='opencode'), '')
            self.assertEqual(due.call_args.args[0]['harnesses'], [{"harness": "opencode"}])
            quota.heartbeat_lines('/unused', {})
            self.assertEqual(len(due.call_args.args[0]['harnesses']), 2)

    def test_quota_error_names_provider_and_model(self):
        a = object.__new__(OpenCodeAgent)
        for language in ('zh','en'):
            a.cfg = {'language': language, 'model': 'ark-coding-plan/doubao-seed-2.1-pro'}
            for why in ('balance','quota_window','rate_limit'):
                text = a.provider_note(why, 'ark-coding-plan', '中文说明 / English note')
                self.assertIn('ark-coding-plan', text)
                self.assertIn('doubao-seed-2.1-pro', text)
                self.assertIn('来源未确认' if language == 'zh' else 'source unconfirmed', text)
                self.assertIn('周期未说明' if language == 'zh' else 'window unspecified', text)

    def test_retired_plan_is_not_a_preset_or_key_entry(self):
        self.assertEqual(bailian.PLANS, ('token-plan',))
        self.assertNotIn('coding-plan', bailian.data())
        self.assertFalse(keys.importable(bailian.RETIRED_KEY))
        rows = next(r for vid, _, r in keys.CATALOG if vid == 'bailian')
        self.assertEqual([n for _, n in rows], ['BAILIAN_TOKEN_PLAN_API_KEY','DASHSCOPE_API_KEY'])

    def test_retirement_notice_once_preserves_old_key_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            fixture = root/'retired.fixture'
            fixture.write_text(bailian.RETIRED_KEY+'=synthetic-retired\n')
            before = fixture.read_bytes()
            notice = Mock()
            st = SimpleNamespace(root=root, write_private=lambda p,b: p.write_bytes(b))
            host = SimpleNamespace(st=st, lang='zh', agent_notice=notice)
            with patch.object(keys,'env_path',return_value=str(fixture)):
                self.assertTrue(bailian.notify_retired(host))
                self.assertFalse(bailian.notify_retired(host))
            self.assertEqual(fixture.read_bytes(),before)
            self.assertEqual(notice.call_count,1)
            self.assertIn('Token Plan',notice.call_args.args[0])
            self.assertNotIn('synthetic-retired',notice.call_args.args[0])


class SupportPolicy(unittest.TestCase):
    def test_healthy_but_silent_prefers_existing_new_session_offer(self):
        source=pathlib.Path(__file__).resolve().parents[2]/'support/system_prompt.md'
        if not source.exists():
            self.skipTest('private support prompt is not part of the public host export')
        text=source.read_text()
        self.assertIn('Healthy service but no AI reply',text)
        self.assertIn('prefer `new_session`',text)
        self.assertIn('unknown',text)


class StopDebounce(unittest.IsolatedAsyncioTestCase):
    def actor(self):
        a=object.__new__(shared.SharedClaudeAgent)
        a.host=SimpleNamespace(lang='zh')
        a.control_name=None
        a.status='working'
        a.state=SimpleNamespace(status='working')
        a.q=asyncio.Queue()
        a.session={'pid':17}
        a.native=SimpleNamespace(pid=17)
        a.master=4
        a.done=asyncio.Event()
        return a

    async def test_duplicate_stops_send_one_escape_and_later_stop_can_work(self):
        a=self.actor()
        with patch.object(shared,'time') as tm, patch.object(shared.os,'write') as write:
            tm.monotonic.return_value=100
            self.assertTrue(await a.halt())
            self.assertTrue(await a.halt())
            self.assertEqual(write.call_count,1)
            self.assertEqual(write.call_args.args[1],b'\x1b')
            tm.monotonic.return_value=103
            self.assertTrue(await a.halt())
            self.assertEqual(write.call_count,2)

    async def test_stop_cannot_escape_a_permission_dialog(self):
        a=self.actor()
        a.state.status='waiting'
        a.cannot_halt=Mock()
        with patch.object(shared.os,'write') as write:
            self.assertFalse(await a.halt())
            write.assert_not_called()
        self.assertFalse(hasattr(a,'_stop_esc_at'))
