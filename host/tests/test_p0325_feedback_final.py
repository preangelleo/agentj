"""P0325 final host contracts: content blocks stay blocks; native failures project safely."""
import _hermetic  # noqa: F401
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from agentj import privacy, privacy_host, application_ingress


class PrivacyHostFinal(unittest.IsolatedAsyncioTestCase):
    def host(self):
        h = NS(agent=NS(), st=NS(log=Mock()), stopped=lambda: False,
               agent_notice=Mock())
        return h

    async def test_blocked_preserves_verdict_and_has_no_failure_projection(self):
        h = self.host()
        verdict = privacy.Verdict('blocked', probability=.91, kind='owner_confirmation',
                                  reason='owner confirmation required')
        req = {'t': 'privacy_check', 'gate': 'feedback', 'draft': {'body': 'safe'}}
        with patch.object(privacy_host, '_origin', return_value={'verified': True}), \
             patch.object(privacy, 'layer2', return_value=verdict):
            result = await privacy_host.handle(h, req, peer_pid=7)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['kind'], 'owner_confirmation')
        self.assertEqual(result['reason'], 'owner confirmation required')
        h.agent_notice.assert_not_called()
        h.st.log.assert_not_called()

    async def test_plaza_remains_without_feedback_retry_card(self):
        h = self.host()
        req = {'t': 'privacy_check', 'gate': 'plaza', 'draft': {'body': 'safe'}}
        with patch.object(privacy_host, '_origin', return_value=None):
            result = await privacy_host.handle(h, req, peer_pid=7)
        self.assertEqual(result['status'], 'unavailable')
        h.agent_notice.assert_called_once()
        self.assertNotIn('card', h.agent_notice.call_args.kwargs)


class OwnerIngress(unittest.TestCase):
    def test_background_owner_tree_never_becomes_feedback_origin(self):
        host = NS(agent=NS(), st=NS(), stopped=lambda: False)
        with patch('agentj.approval_material.verified_caller_origin', return_value=None), \
             patch('agentj.application_ingress._process', return_value=True), \
             patch('agentj.application_ingress._owner_tree', return_value=True):
            self.assertIsNone(privacy_host._origin(host, 42))


class FailureProjection(unittest.TestCase):
    def test_projection_is_formal_payload_free_and_deduplicated(self):
        h = NS(_privacy_receipts=set(), agent_notice=Mock())
        for why in ('broker_thread_required', 'host_unavailable', 'backend_unavailable', 'ordinary_failure'):
            privacy_host.notify_failure(h, 'feedback', why)
        self.assertEqual(h.agent_notice.call_count, 4)
        for call in h.agent_notice.call_args_list:
            text = call.args[0]
            card = call.kwargs['card']
            self.assertEqual(card, {'kind': 'support_feedback_retry'})
            self.assertNotIn('draft', text)
            self.assertNotIn('agentj ', text)
            self.assertNotIn('`', text)
        privacy_host.notify_failure(h, 'feedback', 'ordinary_failure')
        self.assertEqual(h.agent_notice.call_count, 4)

    def test_plaza_projection_is_independent(self):
        h = NS(_privacy_receipts=set(), agent_notice=Mock())
        privacy_host.notify_failure(h, 'plaza', 'ordinary_failure')
        self.assertNotIn('card', h.agent_notice.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
