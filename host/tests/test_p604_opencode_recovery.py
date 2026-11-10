"""OpenCode rescue: ambiguous HTTP 500 must not replay; shared native model labels."""
import _hermetic  # noqa: F401
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import agent_opencode as oc


class Recovery(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_500_never_replays_or_restarts(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.cfg = {'model': 'opencode/test', 'language': 'en'}
        a.persist, a.v2, a.proc, a.sid, a.collect = True, False, object(), 'ses-old', None
        a.cur_send = None
        a.client = SimpleNamespace(request=mock.AsyncMock(side_effect=[(500, {}), (500, {})]))
        a.host = SimpleNamespace(st=SimpleNamespace(set_agent_session=mock.Mock(), log=mock.Mock()),
                                 agent_notice=mock.Mock(), agent_text=mock.Mock(), _fallback_used=False)
        a._note_turn = mock.Mock()
        a._protocol = mock.AsyncMock(return_value=False)
        a._creds_check = mock.AsyncMock()
        a._ensure_started = mock.AsyncMock(return_value=True)
        a._session_ok = mock.AsyncMock(return_value=True)
        a._free_before_prompt = mock.AsyncMock()
        a._kill = mock.AsyncMock()
        a._wait_turn = mock.AsyncMock()
        a._catch_up = mock.AsyncMock()
        a.refresh_usage = mock.Mock(side_effect=lambda: asyncio.create_task(asyncio.sleep(0)))
        a.meter = mock.Mock()
        a._delivery_uncertain = mock.Mock()
        a._heal_delivered = mock.Mock()
        with mock.patch.object(oc, 'turn_body', return_value={'parts': [{'type': 'text', 'text': 'hello'}]}), \
             mock.patch('agentj.native_commands.opencode_request', return_value=None), \
             mock.patch('agentj.main_identity.audit'):
            await a.turn('hello')
            await a.turn('hello')  # even two 500s are not proof of non-execution
            a.host.st.set_agent_session.assert_not_called()
            a._kill.assert_not_awaited()
            self.assertEqual(a.client.request.await_count, 2)
            self.assertEqual(a.host.agent_notice.call_count, 2)
            self.assertIn('not be resent', a.host.agent_notice.call_args.args[0])
            self.assertTrue(a.host._fallback_used)

    def test_shared_native_model_takes_precedence_over_config(self):
        a = object.__new__(oc.OpenCodeAgent)
        a.cfg = {'model': 'opencode/stale', 'session_mode': 'shared'}
        a.session_model = {'providerID': 'opencode', 'id': 'real'}
        self.assertEqual(a.cur_model(), 'opencode/real')
        self.assertEqual(a._model_of({'providerID': 'opencode', 'modelID': 'actual'}),
                         {'providerID': 'opencode', 'modelID': 'actual'})
