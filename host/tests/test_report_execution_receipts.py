"""Observation-only report execution denial receipts; approval behavior is unchanged."""
import _hermetic
import asyncio
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from _testtmp import TEST_TMP
from agentj import action_policy, report, report_local, elevate
from test_p336_approval_level import Base
from _listed_cards import listed


class ReceiptCLI(unittest.TestCase):
    def test_non_linux_missing_helpers_retain_original_approval_lane(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            root=pathlib.Path(tmp)
            with patch.object(report_local.sys,'platform','darwin'),patch.object(report_local,'digest',return_value=report_local.TRUSTED_SHA256),patch.object(pathlib.Path,'is_file',return_value=False),patch('agentj.report_host.request') as request:
                self.assertIsNone(report_local.run(root,['templates'],['templates'],root,capture=True))
                request.assert_not_called()
    def test_legacy_boolean_and_optional_receipt_have_same_decision(self):
        for result, reason in [('approved','device'),('denied','device'),('denied','no_device'),('denied','timeout'),('unavailable',None),('denied',None)]:
            raw={'result':result,'approval_card_shown':None}
            if reason:raw['reason']=reason
            with self.subTest(result=result,reason=reason),patch('agentj.elevate.client_request',return_value=raw):
                old=action_policy.confirm_cli('broker.execute',{})
                receipt={}
                new=action_policy.confirm_cli('broker.execute',{},receipt=receipt)
                self.assertEqual(new,old)
                self.assertEqual(new,result=='approved')
                self.assertEqual(receipt['reason'],reason or ('host_unavailable' if result=='unavailable' else 'unknown'))
                self.assertIsNone(receipt['approval_card_shown'])

    def test_untrusted_receipt_fields_cannot_leak(self):
        with patch('agentj.elevate.client_request',return_value={'result':'denied','reason':'private body','approval_card_shown':True,'device':'private-device','sig':'private-signature'}):
            receipt={}
            self.assertFalse(action_policy.confirm_cli('broker.execute',{},receipt=receipt))
            self.assertEqual(receipt,{'result':'denied','reason':'unknown','approval_card_shown':None})

    def test_malformed_optional_metadata_does_not_change_legacy_boolean(self):
        with patch('agentj.elevate.client_request',return_value={'result':'approved','reason':['not an enum']}):
            self.assertTrue(action_policy.confirm_cli('broker.execute',{},receipt={}))

    def test_report_unknown_engine_retains_denial_with_classified_receipt(self):
        reasons={'offline':'interaction_unavailable','no_device':'interaction_unavailable','host_unavailable':'interaction_unavailable','device':'authorization_denied','timeout':'authorization_timeout','owner_grant_scope':'authorization_scope_missing','group_source':'authorization_source_refused','estop':'execution_stopped','unknown':'authorization_unknown'}
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as tmp:
            engine=pathlib.Path(tmp)/'engine';(engine/'scripts').mkdir(parents=True)
            marker=pathlib.Path(tmp)/'must-not-run'
            (engine/report.ENGINE).write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()')
            for reason, code in reasons.items():
                def deny(*a,receipt=None,**kw):
                    receipt.update(result='denied',reason=reason,approval_card_shown=False if reason=='no_device' else None)
                    return False
                with self.subTest(reason=reason),patch('agentj.action_policy.confirm_cli',side_effect=deny):
                    r=report._run(engine,['templates'],capture=True)
                self.assertEqual(r.returncode,1)
                value=json.loads(r.stderr)
                self.assertEqual(value['code'],code)
                self.assertEqual(value['reason'],reason)
                self.assertNotIn('waiting',r.stderr)
                self.assertFalse(marker.exists())


class AskObservation(Base):
    def _listed(self):
        # 0.30.7 ADR-0307-offlist: off-list Bash no longer cards; these tests are about card receipts (timeout /
        # device deny / reuse), so the vehicle is a listed (tap) card again.
        listed(self, 'Bash')

    def test_actual_initial_offline_and_reused_card_do_not_create_another_card(self):
        self._listed()
        from agentj import approval_level
        self.host.sessions.clear()
        with patch.object(approval_level,'PARK_GRACE',.01):
            initial={}
            self.assertEqual(self.ask('git push origin main',_receipt=initial)['behavior'],'deny')
            self.assertEqual(initial,{'reason':'timeout','approval_card_shown':None})
            count=len(self.host.asks)
            retry={}
            self.assertEqual(self.ask('git push origin main',_receipt=retry)['behavior'],'deny')
            self.assertEqual(retry,{'reason':'timeout','approval_card_shown':None})
            self.assertEqual(len(self.host.asks),count)
            self.assertEqual(count,0)  # completed native timeout does not leave a retired offline parked card

    def test_actual_parked_rewait_timeout_denied_and_stale_allow(self):
        from types import SimpleNamespace
        self.host.ask_ttl=.01
        async def run():
            base={'rid':'b'*32,'agent':'claude','tool':'Bash','input_sha256':'0'*64,'shown_sha256':'0'*64,'cats':[]}
            for decision,expected in ((None,'timeout'),('deny','device'),('allow','unknown')):
                with self.subTest(decision=decision):
                    future=asyncio.get_running_loop().create_future()
                    if decision:future.set_result((decision,self.phone.did,None,None))
                    a=SimpleNamespace(fut=future,tool='Bash',summary='fixture',tool_input={'command':'git push origin main'})
                    receipt={}
                    with patch.object(self.host,'_parked_take',return_value=None):
                        answer=await self.host._parked_rewait(a,base,None,('fixture',),_receipt=receipt)
                    self.assertEqual(answer['behavior'],'deny')
                    self.assertEqual(receipt,{'reason':expected,'approval_card_shown':None})
        asyncio.run(run())

    def test_reused_card_unknown_reason_does_not_claim_denial_or_shown(self):
        self._listed()
        from unittest.mock import AsyncMock
        receipt={}
        # The list adapter never consults the legacy parked grant hooks. The
        # real request times out with an unknown display receipt instead.
        with patch.object(self.host,'_parked_find',side_effect=AssertionError('retired parked hook')) as find, \
             patch.object(self.host,'_parked_rewait',new=AsyncMock(side_effect=AssertionError('retired parked rewait'))) as rewait:
            answer=self.ask('git push origin main',_receipt=receipt)
        self.assertEqual((answer['behavior'],answer['reason']),('deny','timeout'))
        self.assertEqual(receipt,{'reason':'timeout','approval_card_shown':None})
        find.assert_not_called();rewait.assert_not_awaited()
        self.assertEqual(len(self.cards()),1)

    def test_actual_policy_socket_preserves_no_device_reason(self):
        self.st.remove_device(self.phone.did)
        async def run():
            sock=pathlib.Path(self.tmp.name)/'policy.sock'
            service=elevate.Elevator.__new__(elevate.Elevator)
            service.host,self.host.ask_ttl=self.host,.02
            service.st=self.st
            server=await asyncio.start_unix_server(service.on_client,path=str(sock),limit=elevate.MAX_FRAME)
            params={'argv':['/usr/bin/python3','-I','/tmp/engine/scripts/web_report.py','templates'], 'cwd':str(self.work),'inputs':[], 'environment_sha256':'0'*64}
            try:
                receipt={}
                with patch('agentj.names.elevate_sock',return_value=sock):
                    approved=await asyncio.to_thread(action_policy.confirm_cli,'broker.execute',params,st=self.st,receipt=receipt)
                self.assertFalse(approved)
                self.assertEqual(receipt,{'result':'denied','reason':'application_ingress_unverified','approval_card_shown':False})  # unframed broker request refuses before device selection
            finally:
                server.close()
                await server.wait_closed()
        asyncio.run(run())

    def test_actual_no_device_receipt_and_legacy_return_match(self):
        self.st.remove_device(self.phone.did)
        legacy=self.ask('rm -rf ~/data')
        receipt={}
        observed=self.ask('rm -rf ~/data',_receipt=receipt)
        self.assertEqual(observed,legacy)
        self.assertEqual(receipt,{'reason':'no_device','approval_card_shown':False})
        self.assertEqual(self.cards(),[])

    def test_actual_timeout_and_signed_device_deny_are_distinct(self):
        self._listed()
        self.host.ask_ttl=.02
        receipt={}
        timed=self.ask('git push origin main',_receipt=receipt)
        self.assertEqual(timed['behavior'],'deny')
        self.assertEqual(receipt,{'reason':'timeout','approval_card_shown':None})
        self.host.ask_ttl=.4
        denied={}
        result=self.ask('git push origin other',answer=False,_receipt=denied)
        self.assertEqual(result['behavior'],'deny')
        self.assertEqual(denied,{'reason':'device','approval_card_shown':None})
