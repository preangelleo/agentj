"""P590: rejected same-owner admission must never remain silently pending."""
from unittest.mock import patch, AsyncMock
import test_p405_same_owner as baseline
from test_p71_peer_service import pmsg, settle_pending


class Inbound(baseline.SameOwnerTests):
    async def test_filter_refusal_has_durable_reason_receipt_and_notice(self):
        await self.confirm()
        m = pmsg('检查服务')
        self.svc.guards.accept(self.b, m)
        with patch.object(self.svc, '_outbound', AsyncMock(return_value=type('V', (), {'ok':False})())):
            self.assertFalse(await self.svc.same_owner.forward(self.b, m))
        await settle_pending(self.svc)
        row = self.svc.guards.row(self.b, m['mid'])
        self.assertEqual(row['state'], 'blocked')
        self.assertEqual(row['reason'], 'admission_filter')
        self.assertTrue(any(o.get('t') == 'relay_state' and o.get('state') == 'blocked' for _, o in self.svc.net.sent))
        self.assertTrue(any(m['mid'] in n and 'admission_filter' in n for n in self.host.notices))

    async def test_async_exception_is_closed_without_exception_text(self):
        await self.confirm()
        m = pmsg('检查异步服务')
        with patch.object(self.svc, '_outbound', AsyncMock(side_effect=RuntimeError('PRIVATE_FIXTURE_BODY'))):
            self.svc._on_pmsg(self.b, m)
            await settle_pending(self.svc)
        row = self.svc.guards.row(self.b, m['mid'])
        self.assertEqual(row['state'], 'blocked')
        self.assertEqual(row['reason'], 'admission_error')
        self.assertNotIn('PRIVATE_FIXTURE_BODY', str(self.host.notices))

    async def test_relationship_recheck_and_claimed_exception_never_replay(self):
        await self.confirm()
        m = pmsg('关系变化')
        self.svc.guards.accept(self.b, m)
        with patch.object(self.svc.same_owner, 'active', side_effect=[True, False]):
            self.assertFalse(await self.svc.same_owner.forward(self.b, m))
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['reason'], 'relationship')
        n = pmsg('已经认领')
        self.svc.guards.accept(self.b, n)
        self.svc.guards.claim(self.b, n)
        with patch.object(self.svc, '_outbound', AsyncMock(side_effect=RuntimeError('fixture'))):
            await self.svc.same_owner.forward(self.b, n)
        self.assertEqual(self.svc.guards.row(self.b, n['mid'])['state'], 'claimed')
        await self.svc.same_owner.retry()
        self.assertFalse(self.sends)

    async def test_cli_projection_readonly_and_reason_wire_allowlist(self):
        import copy
        await self.confirm()
        m = pmsg('PRIVATE_BODY_MUST_NOT_APPEAR')
        await self.svc.same_owner.forward(self.b, m)
        before = copy.deepcopy(self.svc.guards.rows())
        view = await self.svc.cli({'t': 'requests'})
        self.assertEqual(view['requests'][0]['stage'], 'submitted')
        self.assertNotIn(m['text'], str(view))
        self.assertEqual(before, self.svc.guards.rows())
        from agentj.peer_service import local_cmd
        self.assertEqual(view, local_cmd(self.host.st, self.store, {'t': 'requests'}))
        outgoing = pmsg('合法本地请求')
        outgoing['relay'] = self.svc.guards.outgoing()
        self.svc.guards.remember(self.b, outgoing, 'out')
        for why in ([], {}, 'PRIVATE_EVIL'):
            self.svc.guards.receive(self.b, {'mid':outgoing['mid'], 'state':'pending', 'why':why})
        self.svc.guards.receive(self.b, {'mid':outgoing['mid'], 'state':'blocked', 'why':'PRIVATE_EVIL'})
        self.assertNotIn('reason', self.svc.guards.row(self.b, outgoing['mid'], 'out'))
        self.assertNotIn('PRIVATE_EVIL', str(self.host.notices))

    async def test_submit_failure_stays_retryable_with_reason(self):
        await self.confirm()
        m = pmsg('可恢复提交失败')
        with patch.object(self.host.agent, 'submit', side_effect=RuntimeError('fixture')):
            await self.svc.same_owner.forward(self.b, m)
        row = self.svc.guards.row(self.b, m['mid'])
        self.assertEqual((row['state'], row['reason']), ('pending', 'submit_failed'))
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 1)

    async def test_heartbeat_metadata_and_recent_failure_window(self):
        import time
        from agentj import heartbeat_check
        from types import SimpleNamespace
        await self.confirm()
        self.host.peers = self.svc
        now = self.clock.t
        m = pmsg('心跳只读元数据')
        self.svc.guards.accept(self.b, m)
        self.clock.t += 7200
        now = self.clock.t
        self.svc.guards.block(self.b, m['mid'], 'admission_filter')
        await settle_pending(self.svc)
        before = self.svc.guards.rows()
        with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='')):
            reasons, _ = heartbeat_check.collect(self.host, now)
            self.assertIn('friend_requests', reasons)
            reasons, _ = heartbeat_check.collect(self.host, now + 3601)
            self.assertNotIn('friend_requests', reasons)
        self.assertEqual(before, self.svc.guards.rows())

    async def test_block_is_reloaded_from_private_disk_before_receipt(self):
        import json
        from agentj import diskq
        await self.confirm()
        m = pmsg('检查磁盘持久化')
        self.svc.guards.accept(self.b, m)
        self.svc.guards.block(self.b, m['mid'], 'admission_filter')
        await settle_pending(self.svc)
        from agentj.friends import FriendStore
        restored = FriendStore(self.host.st)
        row = restored._read('relay-requests.json', {})[self.svc.guards.key(self.b, m['mid'])]
        self.assertEqual((row['state'], row['reason']), ('blocked', 'admission_filter'))
        n = pmsg('屏障失败不能确认回执')
        self.svc.guards.accept(self.b, n)
        with patch('agentj.diskq.aflush', AsyncMock(return_value=False)):
            self.svc.guards.block(self.b, n['mid'], 'admission_filter')
            await settle_pending(self.svc)
        self.assertFalse(any(o.get('mid') == n['mid'] and o.get('t') == 'relay_state'
                             for _, o in self.svc.net.sent))

    async def test_accepted_orphan_times_out_blocked_after_recreation(self):
        from agentj.relay_guards import RelayGuards
        await self.confirm()
        m = pmsg('中断后无payload不能执行')
        self.svc.guards.accept(self.b, m)
        self.svc.guards = RelayGuards(self.svc)
        self.clock.t += self.store.settings()['relay_pending_seconds'] + 1
        self.svc.guards.sweep()
        await settle_pending(self.svc)
        row = self.svc.guards.row(self.b, m['mid'])
        self.assertEqual((row['state'], row['reason']), ('blocked', 'admission_interrupted'))
        await self.svc.same_owner.retry()
        self.assertFalse(self.sends)

    async def test_notice_barrier_rereads_claimed_state(self):
        import asyncio
        await self.confirm()
        m = pmsg('屏障竞争')
        self.svc.guards.accept(self.b, m)
        started, release = asyncio.Event(), asyncio.Event()
        async def barrier():
            started.set()
            await release.wait()
            return True
        with patch('agentj.diskq.aflush', barrier):
            self.svc.guards.block(self.b, m['mid'], 'submit_failed', retry=True)
            await started.wait()
            self.svc.guards.claim(self.b, m)
            release.set()
            await settle_pending(self.svc)
        states = [o['state'] for _, o in self.svc.net.sent
                  if o.get('t') == 'relay_state' and o.get('mid') == m['mid']]
        self.assertEqual(states, ['claimed'])

    async def test_outgoing_claimed_stale_pending_has_no_reason_or_notice(self):
        await self.confirm()
        m = pmsg('回执状态竞争')
        m['relay'] = self.svc.guards.outgoing()
        self.svc.guards.remember(self.b, m, 'out')
        self.svc.guards.receive(self.b, {'mid':m['mid'], 'state':'claimed'})
        before = self.svc.guards.row(self.b, m['mid'], 'out')
        notices = list(self.host.notices)
        self.svc.guards.receive(self.b, {'mid':m['mid'], 'state':'pending', 'why':'main_unavailable'})
        self.assertEqual(before, self.svc.guards.row(self.b, m['mid'], 'out'))
        self.assertEqual(notices, self.host.notices)
        self.svc.guards.receive(self.b, {'mid':m['mid'], 'state':'claimed', 'why':'durability'})
        self.assertEqual(self.svc.guards.row(self.b, m['mid'], 'out')['reason'], 'durability')


import unittest


class SyncSweep(unittest.TestCase):
    def test_sync_orphan_sweep_flushes_and_explains_in_chinese(self):
        import tempfile
        import pathlib
        from types import SimpleNamespace
        from agentj.friends import FriendStore
        from agentj.relay_guards import RelayGuards
        from agentj.state import State
        with tempfile.TemporaryDirectory() as d:
            store = FriendStore(State(pathlib.Path(d)))
            notices, sent = [], []
            svc = SimpleNamespace(store=store, clock=lambda:10000000, lang='zh', jobs=set(), asks={},
                                  changed=lambda f:None, notice=notices.append,
                                  _send=lambda f, o:sent.append(o), _finish=lambda *a:None)
            svc.same_owner = SimpleNamespace(active=lambda f:True,
                row=lambda f:store._read('same-owner.json', {}).get(f, {}))
            guard = RelayGuards(svc)
            fid, mid = 'fixture', 'a' * 16
            store._write('relay-requests.json', {guard.key(fid, mid):{
                'friend':fid, 'mid':mid, 'state':'pending', 'direction':'in', 'created':0}})
            store._write('same-owner.json', {fid:{'forwarded':[mid]}})
            with patch('agentj.diskq.flush', return_value=True) as flush:
                guard.sweep()
            flush.assert_called_once()
            self.assertEqual(guard.row(fid, mid)['state'], 'blocked')
            self.assertIn('请求未交给主 Agent', notices[-1])
            self.assertIn('handoff_unknown', notices[-1])
            self.assertEqual(sent[0]['state'], 'blocked')
