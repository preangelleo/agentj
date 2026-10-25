"""P528: synthetic outbound privacy and elevation diagnostics regressions."""
import _hermetic
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from agentj import privacy, support, feedback, peer_guard, elevate


class Privacy(unittest.TestCase):
    def test_recursive_contacts_address_and_index_fragments(self):
        secret = 'syntheticFragmentValue1234567890'
        idx = peer_guard.SecretIndex([secret])
        from agentj.report_privacy import prepare
        raw = {'body': ['call alice@private.invalid or +1 415 555 2671',
                        'Address: 42 Sample Street, Test City', secret[3:23]]}
        draft, hits, reason = prepare(raw, identity=('', ''), index=idx)
        text = json.dumps(draft)
        for value in ('alice@', '415 555', '42 Sample', secret[3:23]):
            self.assertNotIn(value, text)
        self.assertGreaterEqual(sum(hits.values()), 4)
        self.assertIsNone(reason)
        self.assertEqual(prepare(draft, identity=('', ''), index=idx)[0], draft)

    def test_normalised_credential_is_still_refused(self):
        from agentj.report_privacy import prepare
        value = 'sk-' + 'a' * 25
        # Split vendor spellings evade the first pass but must never leave.
        _, _, reason = prepare({'body': value[:3] + ' ' + value[3:]}, index=peer_guard.SecretIndex())
        self.assertIsNotNone(reason)

    def test_support_noncredential_review_is_advisory_after_redaction(self):
        ch = type('Channel', (), {'signed': False, 'send': lambda self, fields: (201, {'thread': 'st_' + 't'*22, 'seq': 1})})()
        out = io.StringIO()
        with patch('agentj.support.privacy.layer2', return_value=privacy.Verdict('blocked', .9, 'contact_or_location', 'private')):
            rc = support.run_send('bug', 'mail alice@private.invalid; button broken', ch=ch, wait=0, out=out)
        self.assertEqual(rc, support.EXIT_PENDING)
        self.assertIn('已脱敏', out.getvalue())
        self.assertIn('st_' + 't'*22, out.getvalue())

    def test_support_credential_review_refuses_without_echo(self):
        ch = type('Channel', (), {'signed': False, 'send': lambda *_: self.fail('must not send')})()
        out = io.StringIO()
        with patch('agentj.support.privacy.layer2', return_value=privacy.Verdict('blocked', .9, 'credential', 'private')):
            self.assertEqual(support.run_send('bug', 'broken', ch=ch, wait=0, out=out), support.EXIT_BLOCKED)
        self.assertIn('credential', out.getvalue())


class Elevation(unittest.TestCase):
    def test_summary_redacts_known_secret_and_contacts(self):
        from agentj.report_privacy import stderr_summary
        value = 'syntheticPasswordValue123'
        with patch('agentj.report_privacy.peer_guard.SecretIndex.build', return_value=peer_guard.SecretIndex([value])):
            text = stderr_summary('permission denied: ' + value + '; alice@private.invalid')
        self.assertIn('permission denied', text)
        self.assertNotIn(value, text)
        self.assertNotIn('alice@', text)
        self.assertLessEqual(len(text), 600)

    def test_authentication_words_in_command_stderr_are_command_failure(self):
        import sys
        from test_f17 import fake_sudo
        with tempfile.TemporaryDirectory() as d:
            password = 'synthetic-password-p528'
            sudo = fake_sudo(pathlib.Path(d), password)
            with patch('agentj.elevate.local_auth_risk', return_value=False):
                res = elevate.run_sudo([sys.executable, '-c', "import sys;sys.stderr.write('Sorry, try again');sys.exit(1)"], bytearray(password.encode()), d, 5, str(sudo))
            self.assertEqual(res['result'], 'done')
            self.assertEqual(res['code'], 1)

    def test_password_echo_never_reaches_result(self):
        import sys
        from test_f17 import fake_sudo
        with tempfile.TemporaryDirectory() as d:
            password = 'synthetic-password-p528'
            sudo = fake_sudo(pathlib.Path(d), password)
            with patch('agentj.elevate.local_auth_risk', return_value=False):
                res = elevate.run_sudo([sys.executable, '-c', "import sys;print('synthetic-password-p528');sys.stderr.write('synthetic-password-p528');sys.exit(1)"], bytearray(password.encode()), d, 5, str(sudo))
            self.assertNotIn(password, json.dumps(res))
            self.assertEqual(res['result'], 'done')

    def test_retry_guidance_mentions_autofill(self):
        self.assertIn('手动输入', elevate.MSG['bad_password'])
        self.assertIn('autofill', elevate.MSG['bad_password'])


class PasswordStream(unittest.TestCase):
    def test_exact_password_is_hidden_across_every_split(self):
        password = b"synthetic-plain-password"
        for cut in range(1, len(password)):
            chunks = []
            watch = type('Watch', (), {'tick':lambda *_: None, 'data':lambda _, i, b:chunks.append(b)})()
            protected = elevate._PasswordWatch(watch, password)
            protected.data(0, b"error: " + password[:cut])
            protected.data(0, password[cut:] + b" denied\n")
            protected.finish()
            text = b"".join(chunks)
            self.assertNotIn(password, text)
            self.assertIn(b"<redacted>", text)
            self.assertIn(b"denied", text)
            self.assertEqual(protected.password, b"")

    def test_final_password_prefix_is_never_flushed(self):
        chunks=[]
        watch=type('Watch', (), {'data':lambda _, i, b:chunks.append(b)})()
        protected=elevate._PasswordWatch(watch, b"synthetic-password")
        protected.data(1, b"error: synthetic-pass")
        protected.finish()
        self.assertNotIn(b"synthetic-pass", b"".join(chunks))


class FeedbackConsent(unittest.TestCase):
    def test_confirmed_contact_report_is_redacted_and_counted(self):
        from test_feedback import DRAFT, SESSION, FakeApi, FakeJev, ENV, FB_ID
        import os
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'AGENTJ_STATE_DIR':root}):
            draft=pathlib.Path(root)/'draft.checked.json'
            draft.write_text(json.dumps(dict(DRAFT, problem='email alice@private.invalid; phone +1 415 555 2671; broken')))
            session=pathlib.Path(root)/'feedback-id';session.write_text(SESSION)
            api=FakeApi();out=io.StringIO()
            rc=feedback.run_send(draft,owner_confirmed=True,session_file=str(session),http_fn=api,
                                 transport=FakeJev(p=.9,kind='contact_or_location'),env=ENV,identity=('', ''),out=out)
            self.assertEqual(rc,0)
            self.assertNotIn(b'alice@',api.calls[0]['body'])
            self.assertNotIn(b'415 555',api.calls[0]['body'])
            self.assertIn('已脱敏 2 处',out.getvalue())
            self.assertIn(FB_ID,out.getvalue())

    def test_owner_confirmation_never_bypasses_model_credential(self):
        from test_feedback import DRAFT, SESSION, FakeApi, FakeJev, ENV
        import os
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'AGENTJ_STATE_DIR':root}):
            draft=pathlib.Path(root)/'draft.checked.json';draft.write_text(json.dumps(dict(DRAFT,problem='broken')))
            session=pathlib.Path(root)/'feedback-id';session.write_text(SESSION)
            api=FakeApi();out=io.StringIO()
            rc=feedback.run_send(draft,owner_confirmed=True,session_file=str(session),http_fn=api,
                                 transport=FakeJev(p=.9,kind='credential'),env=ENV,identity=('', ''),out=out)
            self.assertEqual(rc,feedback.EXIT_BLOCKED)
            self.assertEqual(api.calls,[])
            self.assertIn('credential',out.getvalue())


class PasswordMarker(unittest.TestCase):
    def test_password_matching_marker_text_keeps_auth_classification(self):
        import sys
        from test_f17 import fake_sudo
        with tempfile.TemporaryDirectory() as root:
            password='agentj'
            sudo=fake_sudo(pathlib.Path(root),password)
            result=elevate.run_sudo([sys.executable,'-c',"print('harmless')"],bytearray(password.encode()),root,5,str(sudo))
            self.assertEqual(result['result'],'done')
            self.assertEqual(result['prompts'],1)
