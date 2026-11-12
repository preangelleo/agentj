"""#514: update receipts point to the actual settings group, including offline recovery."""
import _hermetic
import unittest
from agentj import phone_update


class Receipt(unittest.TestCase):
    def test_phone_receipt_actual_menu_and_grey_recovery(self):
        rec = {'reason': 'upgraded', 'from': '0.26.10a4', 'to': '0.26.12', 'counts': (1, 0, 0)}
        for lang, group, button, scroll, grey in (
            ('zh', '网页版 App', '更新并刷新', '向下找到', '灰'),
            ('en', 'Web app', 'Update and reload', 'scroll to', 'grey'),
        ):
            for web in ('same', 'older', None):
                with self.subTest(lang=lang, web=web):
                    text = phone_update.text(rec, lang, web)
                    for required in (group, button, scroll, grey, 'Repair connection' if lang=='en' else '修复连接'):
                        self.assertIn(required, text)
                    self.assertIn('circular-arrow button' if lang == 'en' else '圆形箭头按钮', text)
                    self.assertNotIn('Update & refresh', text)
                    self.assertNotIn('一切正常', text)
                    self.assertNotIn('everything is working', text)
                    self.assertNotIn('phone/web page is already', text)

    def test_failure_receipt_explains_stage_without_leaking_untrusted_detail(self):
        for lang, expected in (('zh', '缓存文件夹没有写权限'), ('en', 'upgrade cache is not writable')):
            text = phone_update.text({'result': 'failed', 'reason': 'download_failed', 'step': 'download',
                                      'errno': 13, 'cache_status': 'miss', 'detail': 'PRIVATE_DETAIL'}, lang)
            self.assertIn(expected, text)
            self.assertNotIn('PRIVATE_DETAIL', text)
            self.assertNotIn('download_failed', text)
            self.assertNotIn('```', text)
        for lang, expected in (('zh', '暂时查不到新版'), ('en', 'could not check the latest version')):
            text = phone_update.text({'reason': 'unknown', 'detail': 'PRIVATE_DETAIL'}, lang)
            self.assertIn(expected, text)
            self.assertNotIn('unknown', text)
            self.assertNotIn('PRIVATE_DETAIL', text)

    def test_current_receipt_does_not_claim_upgrade_or_restart(self):
        text = phone_update.text({'reason': 'already_current', 'to': '0.26.12'}, 'en')
        self.assertNotIn('grey', text)
        self.assertNotIn('restart', text)
