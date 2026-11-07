"""Support fallback retains actionable text and removes whole credential lines."""
import _hermetic
import io
import unittest
from types import SimpleNamespace
from unittest import mock
from agentj import peer_guard, privacy, report_privacy, support

class LocalRules(unittest.TestCase):
    def test_mask_personal_fields_but_keep_bug_diagnosis(self):
        draft,hits=support.prepare('姓名: 张三\nname: Alice Smith\n邮箱: alice@private.invalid\n电话: +8613812345678\n路径 /Users/alice/private/x.txt\nFace ID完成后卡片仍停留',None,identity=('my-host','my-user'))
        text=draft['body']
        for private in ('张三','Alice Smith','alice@private.invalid','13812345678','/Users/alice/private/x.txt'):
            self.assertNotIn(private,text)
        self.assertIn('Face ID完成后卡片仍停留',text)
        self.assertGreater(hits['name'],0)

    def test_entire_credential_line_deleted_before_redaction(self):
        draft,hits=support.prepare('Before useful issue\nAPI_KEY=fixture-example-password-0123456789\nAfter useful issue',None,identity=('my-host','my-user'))
        self.assertNotIn('API_KEY',draft['body'])
        self.assertIn('Before useful issue',draft['body'])
        self.assertIn('After useful issue',draft['body'])
        self.assertEqual(hits['credential_line'],1)
        self.assertIsNone(peer_guard.credential_shape(draft['body']))

    def test_multiline_private_key_block_removed(self):
        mark='-' * 5
        text=f'Issue retained\n{mark}BEGIN PRIVATE KEY{mark}\nnot-a-real-key\n{mark}END PRIVATE KEY{mark}\nNext diagnosis'
        draft,_=support.prepare(text,None,identity=('my-host','my-user'))
        self.assertNotIn('not-a-real-key',draft['body'])
        self.assertIn('Next diagnosis',draft['body'])


    def test_known_secret_deletes_whole_line_without_shape(self):
        fixture_value = 'owner-chosen-fixture-value'
        index = peer_guard.SecretIndex([fixture_value])
        with mock.patch.object(peer_guard.SecretIndex, 'build', return_value=index):
            draft,hits=support.prepare('Useful bug context\nprivate note '+fixture_value+' never publish\nError EADDRINUSE',None,
                                       identity=('my-host','my-user'))
        self.assertNotIn('private note', draft['body'])
        self.assertNotIn('never publish', draft['body'])
        self.assertIn('Error EADDRINUSE', draft['body'])
        self.assertEqual(hits['credential_line'],1)

    def test_all_secret_text_is_not_sent_as_empty_ticket(self):
        ch=SimpleNamespace(signed=True,st=None,send=mock.Mock())
        with self.assertRaises(support.SupportError):
            support.run_send('bug','API_KEY=fixture-example-password-0123456789',ch=ch,env={},out=io.StringIO())
        ch.send.assert_not_called()

    def test_unsigned_unavailable_uses_local_rules_without_extra_confirmation(self):
        ch=SimpleNamespace(signed=False,st=None,send=mock.Mock(return_value=(201,{"thread":"st_"+"T"*22,"seq":1})))
        with mock.patch.object(support,'_full_access',return_value=True), \
             mock.patch.object(privacy,'layer2',return_value=privacy.Verdict('unavailable')):
            rc=support.run_send('bug','Error EADDRINUSE',ch=ch,env={},out=io.StringIO(),wait=0,identity=('host','user'))
        self.assertEqual(rc,support.EXIT_PENDING)
        self.assertIn(privacy.LOCAL_REVIEW_NOTE,ch.send.call_args.args[0]["body"])

    def test_personal_mask_is_idempotent(self):
        text='姓名: 张三\nMy name is Alice Smith\nIssue EADDRINUSE'
        once=report_privacy.support_rules(text,index=peer_guard.SecretIndex())[0]
        twice=report_privacy.support_rules(once,index=peer_guard.SecretIndex())[0]
        self.assertEqual(once,twice)
        self.assertNotIn('Alice Smith',once)
        self.assertIn('Issue EADDRINUSE',once)


class FallbackDelivery(unittest.TestCase):
    def test_unavailable_signed_full_access_sends_only_locally_redacted_body_and_attachment(self):
        import test_support as fixtures
        f=fixtures.Support();f.setUp();self.addCleanup(f.doCleanups)
        api=fixtures.FakeAPI(f.st)
        fixture_value='owner-chosen-fixture-value'
        index=peer_guard.SecretIndex([fixture_value])
        text=('EADDRINUSE after restart\n姓名: 张三\nEmail alice@private.invalid\n'
              'Phone +8613812345678\nPath /Users/alice/private/file.txt\nKnown '+fixture_value+' removed line')
        with mock.patch.object(peer_guard.SecretIndex,'build',return_value=index), \
             mock.patch.object(privacy,'layer2',return_value=privacy.Verdict('unavailable',reason='no key')), \
             mock.patch.object(support,'_full_access',return_value=True):
            rc=support.run_send('bug',text,ch=f.chan(api),env={},identity=(fixtures.HOST,fixtures.USER),wait=0,
                attach_doctor=True,doctor_fn=lambda:'doctor port_busy\n姓名: 李四\nsecret '+fixture_value,
                out=io.StringIO())
        self.assertEqual(rc,support.EXIT_PENDING)
        self.assertEqual(len(api.sent),1)
        body,attach=api.sent[0]['body'],api.sent[0]['attach']
        self.assertIn('本机规则脱敏，未经模型审核',body)
        self.assertIn('EADDRINUSE after restart',body)
        self.assertIn('doctor port_busy',attach)
        for value in ('张三','李四','alice@private.invalid','13812345678','/Users/alice/private/file.txt',fixture_value):
            self.assertNotIn(value,body+attach)
        self.assertNotIn('Known',body)
        self.assertIsNone(peer_guard.credential_shape(body+attach))
