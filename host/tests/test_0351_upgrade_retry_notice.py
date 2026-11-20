"""Only promise another automatic attempt when the actual scheduler accepts this host."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentj import auto_update, phone_update, upgrade_diag
from agentj.state import State


WORDS = {'en': 'will retry at the next eligible idle check', 'zh': '会自动再试'}


class RetryNotice(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.st = State(Path(folder.name))
        self.rec = {'result': 'failed', 'from': '0.30.50', 'to': '0.30.51',
                    'phase': 'prepared', 'step': 'install', 'reason': 'dependency_download_failed',
                    'uv_repair_doctor': [14, 0, 0], 'tell_owner': True}
        self.enterContext(patch.object(auto_update, '__version__', '0.30.50'))

    def assert_receipts(self, rec, expected):
        for lang, words in WORDS.items():
            self.st.write_private(self.st.root / phone_update.RESULT, json.dumps(rec).encode())
            phone = phone_update.text(phone_update.take(self.st), lang)
            auto_update.write(self.st, {'result': rec, 'notice_pending': True})
            automatic = auto_update.take_notice(self.st, lang, harness=None)
            self.assertIsNone(phone_update.take(self.st))
            self.assertIsNone(auto_update.take_notice(self.st, lang, harness=None))
            for msg in (phone, automatic):
                self.assertIsInstance(msg, str)
                self.assertEqual(words in msg, expected, msg)
                if 'uv_repair_doctor' in rec:
                    self.assertIn(upgrade_diag.UPGRADE_REPAIR_GUIDE, msg)
        return phone

    def test_network_failure_and_scheduler_use_same_controls(self):
        now = datetime.datetime.now().replace(hour=3, minute=30).timestamp()
        for auto, instance, primary, expected in ((True, 1, True, True), (False, 1, True, False),
                                                   (True, 2, True, False), (True, 2, False, True)):
            with self.subTest(auto=auto, instance=instance, primary=primary), \
                    patch.object(auto_update, 'enabled', return_value=auto), \
                    patch('agentj.instance.current', return_value=instance), \
                    patch.object(auto_update, '_primary_present', return_value=primary):
                self.assert_receipts(self.rec, expected)
                auto_update.write(self.st, {'phone_at': now - 86400})
                self.assertEqual(auto_update.due(self.st, now=now, rand=lambda _: 0), expected)

    def test_no_hint_on_ineligible_receipts(self):
        with patch.object(auto_update, 'schedules_installs', return_value=True):
            for change in ({'reason':'permission_denied'}, {'reason':'rollback_failed'},
                           {'reason':'installer_incompatible'}, {'to':'0.30.50'}, {'to':'unknown'}, {'to':7}):
                with self.subTest(change=change):
                    self.assert_receipts({**self.rec, **change}, False)
            for reason in ('signature_or_hash_mismatch', 'dependency_download_failed'):
                old = dict(self.rec, reason=reason)
                del old['uv_repair_doctor']
                self.assert_receipts(old, False)
            self.assertFalse(auto_update.uv_retry_scheduled(self.st, {**self.rec, 'from':'0.30.47'}))

    def test_existing_privacy_choice_and_upgrade_result(self):
        thread = 'st_' + 'x' * 22
        generic = {k:v for k,v in self.rec.items() if k != 'uv_repair_doctor'}
        with patch.object(auto_update, 'schedules_installs', return_value=True):
            for suffix in (' metadata only', ''):
                rec = {**generic, 'support':f'sent ({thread})' + suffix}
                for lang, needle in (('en', 'no logs'), ('zh', '不含日志')):
                    self.st.write_private(self.st.root / phone_update.RESULT, json.dumps(rec).encode())
                    msg = phone_update.text(phone_update.take(self.st), lang)
                    self.assertEqual(needle in msg, bool(suffix))
                    self.assertIn('/update', msg)
                    self.assertNotIn(WORDS[lang], msg)
            msg = auto_update.notice({**self.rec, 'result':'ok', 'uv_repaired':True}, 'en')
            self.assertIn('updated uv', msg)
            self.assertNotIn(WORDS['en'], msg)
