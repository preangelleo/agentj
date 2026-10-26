"""P535: upgrade health excludes onboarding; failures retain a phone repair action."""
import _hermetic
import asyncio
import unittest
from unittest.mock import Mock, patch
from agentj import update, phone_update, slash
import test_p76_update as fixtures

class Receipt(unittest.TestCase):
    def test_same_blocking_classification_as_doctor_not_starter_or_warning_count(self):
        rows=[{'id':'version','status':'ok'},{'id':'first-run','status':'warn'},
              {'id':'root-structure','status':'fail'},
              {'id':'main-inject','status':'fail','summary':'no verified main Agent launch yet'}]
        self.assertFalse(update.upgrade_health(rows)[0])
        for lang in ('zh','en'):
            for render in (lambda: update.completion_text({'to':'2'},(999,10,7),lang,rows),
                           lambda: phone_update.text({'to':'2','reason':'upgraded','counts':(999,10,7),'checks':rows},lang)):
                text=render()
                self.assertIn('一切正常' if lang=='zh' else 'everything is working',text)
                for forbidden in ('doctor','7','10','待办','remain'):self.assertNotIn(forbidden,text)

    def test_blockers_count_is_derived_from_checks_with_safe_names(self):
        rows=[{'id':'serve','status':'fail','summary':'PRIVATE'}, {'id':'state','status':'fail'},
              {'id':'first-run','status':'warn'}, {'id':'root-structure','status':'fail'}]
        for lang in ('zh','en'):
            text=update.completion_text({'to':'2'},(0,99,99),lang,rows)
            self.assertIn('2',text);self.assertNotIn('99',text);self.assertNotIn('PRIVATE',text)
            self.assertIn('一键修复' if lang=='zh' else 'Repair',text)
            self.assertNotIn('doctor',text)
            self.assertNotIn('一切正常' if lang=='zh' else 'everything is working',text)

    def test_unknown_and_unfinished_check_never_claim_success(self):
        for rows in (None,[],[{'status':'fail'}]):
            text=update.completion_text({'to':'2'},update.doctor_counts(rows),'en',rows)
            self.assertNotIn('everything is working',text)
            self.assertIn('Repair',text)

class HostReceipt(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.Upgrade.setUp
    async def test_marker_and_phone_receipts_check_on_host_without_starters(self):
        import json
        from agentj import __version__
        for via_phone in (False,True):
            self.h.cmd_card.reset_mock()
            if via_phone:
                self.st.write_private(self.st.root/phone_update.RESULT,json.dumps({'to':__version__,'reason':'upgraded','result':'ok'}).encode())
            else:update.write_marker(self.st,'0.1',__version__)
            with patch('agentj.doctor.run',return_value=[{'id':'serve','status':'fail'}]) as doctor, \
                 patch.object(self.h,'_starters_line',side_effect=AssertionError('not an upgrade checklist')):
                await self.h.upgraded_notice()
            doctor.assert_called_once_with(self.st)
            self.assertIn('后台连接',self.h.cmd_card.call_args.args[1].text)
            self.assertEqual(self.h.cmd_card.call_args.args[1].kind,'error')

    async def test_real_command_card_exposes_fixed_action(self):
        from agentj.serve import Host
        with patch.object(self.h,'_remember',return_value={}) as remember, patch.object(self.h,'hist_add'):
            Host.cmd_card(self.h,'update',slash.Result('连接有问题','error'))
        self.assertEqual(remember.call_args.kwargs['x']['open'],'upgrade_repair')
