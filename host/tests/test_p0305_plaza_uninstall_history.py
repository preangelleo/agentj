"""Actual signed bundle install -> signed recoverable uninstall -> reinstall trust checks."""
import _hermetic
import asyncio
from pathlib import Path
from unittest import mock
from agentj import application_ingress,approvals,market,plaza_host
from test_plaza_install_card import CardFixture
from test_p0305_plaza_uninstall import Journey as UninstallFixture
from market_fixtures import TestKey


class HistoryJourney(CardFixture):
    run_uninstall=UninstallFixture.run_uninstall

    def setUp(self):
        super().setUp()
        from agentj import plaza
        scope=mock.patch.object(plaza,'seat_gate',return_value=None)
        scope.start();self.addCleanup(scope.stop)
        self.key=TestKey()
        self.host.ask_ttl=1
        self.target=self.e.home/'installed/demo-flow'

    def package_at(self,version='1.2.0',signed=True):
        data,manifest=self.package(version=version)
        row=self.srv.packages['demo-flow']['detail']
        row['signature']=self.key.sign_package(data,manifest) if signed else None
        row['track']='official' if signed else 'community'
        row['certified']=signed

    def request(self,version=None):
        self.sent.clear()
        async def go():
            async def call():
                writer=mock.Mock();writer.is_closing.return_value=False
                with application_ingress._enter(self.host,writer,{},lambda:True,'terminal'):
                    return await plaza_host.handle(self.host,{'name':'demo-flow','harness':'codex',
                        'workspace':str(self.target.parent),'version':version},post=self.srv,get=self.srv.get,
                        keyring=self.key.keyring)
            task=asyncio.create_task(call())
            for _ in range(500):
                await asyncio.sleep(.005)
                if self.cards() or task.done():break
            if self.cards():
                await self.host._app(self.s,self.phone.answer(self.cards()[-1],True))
            return await asyncio.wait_for(task,4)
        return asyncio.run(go())

    def install_and_uninstall(self,signed=True):
        self.package_at(signed=signed)
        installed=self.request()
        self.assertTrue(installed['ok'],installed)
        self.assertEqual(len(self.cards()),1)
        self.assertTrue(self.target.joinpath('AGENTS.md').is_file())
        self.assertEqual(market.read_installed(self.st)[0]['certified'],signed)
        self.sent.clear()
        removed=self.run_uninstall()
        self.assertTrue(removed['ok'],removed)
        self.assertEqual(len(self.cards()),1)
        self.assertFalse(self.target.exists())
        self.assertEqual(market.read_installed(self.st),[])
        self.assertEqual(approvals.check_record(self.st,approvals.read_log(self.st)[-1]),'ok')
        self.sent.clear()

    def test_signed_uninstall_then_unsigned_reinstall_refuses_before_card(self):
        self.install_and_uninstall()
        self.package_at('1.3.0',signed=False)
        result=self.request()
        self.assertEqual(result['rc'],market.EXIT_SIGNATURE,result)
        self.assertFalse(self.cards());self.assertFalse(self.target.exists())
        self.assertEqual(market.read_installed(self.st),[])

    def test_uninstalled_version_highwater_refuses_implicit_downgrade_before_card(self):
        self.install_and_uninstall()
        self.package_at('1.1.0')
        result=self.request()
        self.assertFalse(result['ok'],result)
        self.assertFalse(self.cards());self.assertFalse(self.target.exists())
        self.assertIn('DOWNGRADE',result['out'].upper().replace('降级','DOWNGRADE'))

    def test_signed_new_version_reinstall_still_uses_normal_card(self):
        self.install_and_uninstall()
        self.package_at('1.3.0')
        result=self.request()
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(self.cards()),1)
        self.assertEqual(market.read_installed(self.st)[0]['version'],'1.3.0')
        self.assertTrue(self.target.joinpath('AGENTS.md').is_file())

    def test_ordinary_unsigned_reinstall_still_uses_normal_card(self):
        self.install_and_uninstall(signed=False)
        self.package_at('1.3.0',signed=False)
        result=self.request()
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(self.cards()),1)
        self.assertFalse(market.read_installed(self.st)[0]['certified'])

    def test_explicit_signed_downgrade_keeps_existing_card_contract(self):
        self.install_and_uninstall()
        self.package_at('1.1.0')
        result=self.request(version='1.1.0')
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(self.cards()),1)
        self.assertIn('DOWNGRADE',self.cards()[-1]['summary'])
