"""#520: feedback retains locally redacted support text, never fake owner_informed."""
import _hermetic  # noqa: F401
import io
import json
import unittest
from unittest import mock
from agentj import feedback, host_diagnostics, privacy, support
import test_support as fixtures

DRAFT = {'stage': 'other', 'host_form': 'unknown', 'os': 'withheld', 'agent_kind': 'other',
         'agent_version': 'unknown', 'install_md_version': 'unknown', 'problem': 'private customer story',
         'resolved': False, 'owner_informed': False}


class Feedback520(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Support(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.api = fixtures.FakeAPI(self.f.st)
        self.feedback_http = mock.Mock(side_effect=AssertionError('legacy API must not receive automatic owner_informed'))

    def send(self, verdict, *, full=True, draft=None, owner_confirmed=False):
        out = io.StringIO()
        with mock.patch.object(support, '_full_access', return_value=full), \
             mock.patch.object(support, 'channel_for', return_value=self.f.chan(self.api)), \
             mock.patch.object(privacy, 'layer2', side_effect=[verdict, privacy.Verdict('unavailable', reason='no key')]):
            rc = feedback.run_send(None, raw_text=json.dumps(draft or DRAFT), state=self.f.st, env={},
                                   identity=('devbox', 'alice'), http_fn=self.feedback_http, out=out,
                                   owner_confirmed=owner_confirmed)
        return rc, out.getvalue()

    def test_full_unavailable_sends_redacted_report_without_owner_informed(self):
        rc, out = self.send(privacy.Verdict('unavailable', reason='personal reviewer error'))
        self.assertEqual(rc, support.EXIT_PENDING)
        self.feedback_http.assert_not_called()
        self.assertEqual(len(self.api.sent), 1)
        text = json.dumps(self.api.sent[0])
        for raw in ('owner_informed', 'personal reviewer error', 'attach'):
            self.assertNotIn(raw, text)
        self.assertIn('本机规则脱敏，未经模型审核', self.api.sent[0]['body'])
        self.assertIn('not model-reviewed', out)
        self.assertNotIn('owner confirmed', out)
        records = [json.loads(x) for x in self.f.st.log_path.read_text().splitlines()]
        self.assertTrue(any(x.get('ev') == 'feedback_autonomous' and x.get('by') == 'signed_support_channel' for x in records))

    def test_full_personal_flag_and_layer1_changes_never_send_raw(self):
        draft = {**DRAFT, 'problem': 'personal email person@example.invalid', 'owner_informed': True}
        rc, out = self.send(privacy.Verdict('blocked', reason='personal', kind='personal_contact'),
                            draft=draft, owner_confirmed=True)
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertNotIn('person@example.invalid', json.dumps(self.api.sent))
        self.assertNotIn('owner confirmed', out)
        self.feedback_http.assert_not_called()

    def test_full_clean_review_with_no_owner_read_uses_signed_support_report(self):
        rc, _ = self.send(privacy.Verdict('ok'))
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertIn('private customer story', json.dumps(self.api.sent))
        self.feedback_http.assert_not_called()

    def test_payload_owner_informed_true_is_not_proof_of_a_human_read(self):
        rc, _ = self.send(privacy.Verdict('ok'), draft={**DRAFT, 'owner_informed': True})
        self.assertEqual(rc, support.EXIT_PENDING)
        self.feedback_http.assert_not_called()
        self.assertNotIn('owner_informed', json.dumps(self.api.sent))

    def test_real_credential_still_blocks_even_if_owner_confirmed(self):
        for kind in ('credential', None):
            rc, _ = self.send(privacy.Verdict('blocked', reason='credential', kind=kind), owner_confirmed=True)
            self.assertEqual(rc, feedback.EXIT_BLOCKED)
            self.assertFalse(self.api.sent)
            self.feedback_http.assert_not_called()

    def test_nonfull_model_unavailable_is_optional(self):
        rc, out = self.send(privacy.Verdict('unavailable'), full=False, draft={**DRAFT, 'owner_informed': True})
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertIn('未经模型审核', self.api.sent[0]['body'])
        self.assertNotIn('owner confirmed', out)
        self.feedback_http.assert_not_called()

    def test_host_diagnostics_does_not_forge_human_confirmation(self):
        with mock.patch.object(support, '_full_access', return_value=True), \
             mock.patch.object(feedback, 'run_send', return_value=5) as send:
            res = host_diagnostics.execute(self.f.st, {'op': 'feedback_send', 'raw': json.dumps(DRAFT), 'owner_confirmed': True})
        self.assertEqual(res['exit'], 5)
        self.assertFalse(send.call_args.kwargs['owner_confirmed'])
        self.assertIs(send.call_args.kwargs['state'], self.f.st)


if __name__ == '__main__':
    unittest.main()
