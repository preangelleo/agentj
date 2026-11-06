"""#520: full-access support degrades privacy failures to content-free metadata."""
import _hermetic  # noqa: F401
import io
import json
import unittest
from unittest import mock
from agentj import approval_level, host_diagnostics, preferences, privacy, support
import test_support as fixtures


class Support520(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.Support()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.api = fixtures.FakeAPI(self.f.st)

    def send(self, verdict, full=True, **kwargs):
        out = io.StringIO()
        with mock.patch.object(privacy, 'layer2', return_value=verdict), \
             mock.patch.object(support, '_full_access', return_value=full):
            rc = support.run_send('bug', 'Private customer story at a secret location',
                                  ch=self.f.chan(self.api), attach_doctor=True,
                                  doctor_fn=lambda: 'Private doctor detail', wait=0, out=out,
                                  identity=(fixtures.HOST, fixtures.USER), **kwargs)
        return rc, out.getvalue()

    def test_full_unavailable_sends_only_fixed_metadata_and_logs_no_human_answer(self):
        rc, out = self.send(privacy.Verdict('unavailable', reason='private reviewer reason'))
        self.assertEqual(rc, support.EXIT_PENDING)
        sent = self.api.sent[0]
        self.assertNotIn('attach', sent)
        self.assertEqual(sent['body'], 'Agent J support request\nprivacy_review: unavailable\npayload: withheld\nmode: automatic-metadata-only')
        self.assertNotIn('Private', json.dumps(sent))
        self.assertNotIn('private reviewer reason', json.dumps(sent))
        self.assertIn('owner did not answer', out)
        self.assertNotIn('owner confirmed', out)
        rows = [json.loads(x) for x in self.f.st.log_path.read_text().splitlines()]
        auto = [x for x in rows if x.get('ev') == 'support_autonomous']
        self.assertEqual(len(auto), 1)
        self.assertEqual(auto[0]['by'], 'full_access_no_owner_answer')
        self.assertEqual(auto[0]['result'], 'metadata_only')

    def test_full_personal_flag_never_sends_raw_even_with_owner_confirmed_flag(self):
        rc, out = self.send(privacy.Verdict('blocked', reason='personal', kind='personal_contact'), owner_confirmed=True)
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertIn('privacy_review: blocked', self.api.sent[0]['body'])
        self.assertNotIn('customer', json.dumps(self.api.sent))
        self.assertNotIn('owner confirmed', out)

    def test_real_credential_flag_still_blocks_every_send(self):
        for kind in ('credential', None):
            self.api.sent.clear()
            rc, _ = self.send(privacy.Verdict('blocked', reason='credential', kind=kind), owner_confirmed=True)
            self.assertEqual(rc, support.EXIT_BLOCKED)
            self.assertEqual(self.api.sent, [])

    def test_residual_credential_never_reaches_transport(self):
        with mock.patch.object(support, 'prepare', side_effect=support.SupportError('residual credential')):
            with self.assertRaises(support.SupportError):
                self.send(privacy.Verdict('unavailable'))
        self.assertEqual(self.api.sent, [])

    def test_nonfull_privacy_failure_keeps_existing_confirmation(self):
        rc, out = self.send(privacy.Verdict('unavailable', reason='offline'), full=False)
        self.assertEqual(rc, support.EXIT_UNAVAILABLE)
        self.assertEqual(self.api.sent, [])
        self.assertIn('--owner-confirmed', out)

    def test_unsigned_full_configuration_does_not_enable_fallback(self):
        with mock.patch.object(preferences, 'effective', return_value={'approval': {'level': 'harness'}}):
            self.assertFalse(support._full_access(self.f.st))
        self.assertFalse(support._full_access(None))

    def test_host_diagnostics_never_invents_owner_confirmation(self):
        req = {'op': 'support', 'kind': 'bug', 'text': 'a problem', 'owner_confirmed': True}
        with mock.patch.object(support, '_full_access', return_value=True), \
             mock.patch.object(support, 'channel_for', return_value=self.f.chan(self.api)), \
             mock.patch.object(support, 'run_send', return_value=5) as send:
            self.assertEqual(host_diagnostics.execute(self.f.st, req)['exit'], 5)
        self.assertFalse(send.call_args.kwargs['owner_confirmed'])


if __name__ == '__main__':
    unittest.main()
