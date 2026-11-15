"""Active same-owner report -> real Host history -> main queue; source revalidation failures.

The native transport and bilateral owner consent use the existing local fixture.
No production credential files or customer state are read.
"""
import _hermetic  # noqa: F401
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_p405_same_owner as baseline
from test_p71_peer_service import pmsg, settle_pending
from agentj import peer_guard
from agentj.serve import Host


class Reports(baseline.SameOwnerTests):
    def actual_host(self):
        host = Host(self.host.st, events='jsonl', read_stdin=False)
        host.agent = SimpleNamespace(submit=self.sends.append, kind='codex', status='idle',
                                     native_source={'kind':'terminal','role':'owner'})
        host.peer_broadcast = self.host.peer_broadcast
        host.push_notify = self.host.push_notify
        host.agent_notice = self.host.agent_notice
        self.svc.host = host
        return host

    def fixture_index(self):
        root=Path(self.tmp.name)/'fixture'; root.mkdir()
        source=root/'provider-settings.conf'
        source.write_text('API_KEY=fixture_initial_material_0123456789abcdef\n')
        build=peer_guard.SecretIndex.build
        fresh=lambda *a,**k: build(environ={}, files=[str(source)], home=str(root),service_env=False)
        self.svc.index=fresh()
        return source,fresh

    async def test_changed_secret_source_refreshes_once_and_delivers_proactive_report(self):
        await self.confirm()
        host=self.actual_host()
        source,fresh=self.fixture_index()
        source.write_text('API_KEY=fixture_rotated_material_abcdef0123456789\n')
        message=pmsg('这台设备已自动更新到新版本，服务正常')
        with patch.object(peer_guard.SecretIndex,'build',side_effect=fresh) as rebuild:
            self.svc._on_pmsg(self.b,message)
            await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1)
        self.assertEqual(rebuild.call_count,1)
        send=self.sends[0]
        self.assertEqual(send.source['kind'],'same_owner')
        self.assertIn(message['text'],send.text)
        self.assertEqual(host.hist.get(send.turn)['src']['mid'],message['mid'])
        self.assertFalse(self.svc.asks,'active same-owner report needs no handover approval')
        self.assertFalse(self.sess.calls,'not sent to an isolated friend model')
        self.svc._on_pmsg(self.b,message)
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends),1,'transport retry does not duplicate main queue admission')

    async def test_incomplete_scan_has_specific_safe_reason_and_never_delivers(self):
        await self.confirm()
        source,fresh=self.fixture_index()
        source.unlink();source.mkdir()  # genuinely unreadable as a file; do not mock the scanner
        message=pmsg('设备服务正常')
        with patch.object(peer_guard.SecretIndex,'build',side_effect=fresh):
            self.svc._on_pmsg(self.b,message)
            await settle_pending(self.svc)
        row=self.svc.guards.row(self.b,message['mid'])
        self.assertEqual((row['state'],row['reason']),('blocked','admission_secret_scan'))
        self.assertFalse(self.sends)
        receipts=[o for _,o in self.svc.net.sent if o.get('t')=='relay_state' and o.get('mid')==message['mid']]
        self.assertEqual(receipts[-1]['why'],'admission_secret_scan')
        self.assertNotIn(str(source),str(self.host.notices))

    async def test_fresh_scan_still_blocks_rotated_material(self):
        await self.confirm()
        source,fresh=self.fixture_index()
        material='fixture_rotated_material_abcdef0123456789'
        source.write_text('API_KEY='+material+'\n')
        with patch.object(peer_guard.SecretIndex,'build',side_effect=fresh):
            self.svc._on_pmsg(self.b,pmsg('备份内容 '+material))
            await settle_pending(self.svc)
        self.assertFalse(self.sends)
        self.assertFalse(self.pages)
        self.assertNotIn(material,str(self.host.notices))

    async def test_revoked_relation_rejects_report_before_main_admission(self):
        await self.confirm()
        self.svc.same_owner.consent(self.b,False)
        self.assertFalse(await self.svc.same_owner.forward(self.b,pmsg('设备服务正常')))
        self.assertFalse(self.sends)

    async def test_other_owner_report_stays_in_friend_channel(self):
        self.svc._on_pmsg(self.b,pmsg('设备服务正常'))
        await settle_pending(self.svc)
        self.assertFalse(self.sends)
        self.assertTrue(self.sess.calls or self.svc.asks)


if __name__=='__main__':unittest.main()
