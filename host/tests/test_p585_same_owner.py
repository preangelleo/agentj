"""P585 authenticated same-owner handoff must survive unavailable main consumers."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_p405_same_owner as existing
from test_p71_peer_service import pmsg, settle, settle_pending


class Handoff(existing.SameOwnerTests):
    async def test_active_same_owner_bypasses_ordinary_hold(self):
        await self.confirm()
        with self.store._edit('friends.json', {}) as rows:
            rows[self.b]['hold_for_owner'] = True
        m = pmsg('检查合成服务')
        self.svc._on_pmsg(self.b, m)
        await settle_pending(self.svc)
        self.assertEqual(len(self.sends), 1)
        self.assertFalse(self.svc.asks)
        self.assertEqual(self.pages[-1][0]['text'], m['text'])

    async def test_disconnected_pending_retries_once_on_same_card(self):
        await self.confirm()
        m = pmsg('等待主线程恢复')
        agent, self.host.agent = self.host.agent, None
        self.host.hist_update = lambda *a, **kw: None
        await self.svc.same_owner.forward(self.b, m)
        self.host.agent = agent
        await self.svc.same_owner.retry()
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(len(self.pages), 1)
        self.assertEqual(self.sends[0].turn, 1)

    async def test_pending_survives_ordinary_lease_and_restart(self):
        from agentj.same_owner import SameOwner
        await self.confirm()
        m = pmsg('重启仍须办理')
        await self.svc.same_owner.forward(self.b, m)
        self.clock.t += self.store.settings()['relay_pending_seconds'] + 10
        self.svc.guards.sweep()
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'pending')
        self.svc.same_owner = SameOwner(self.svc)
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 2)
        self.assertEqual(len(self.pages), 1)

    async def test_status_timeout_retries_but_claimed_never_replays(self):
        await self.confirm()
        m = pmsg('状态暂时离线')
        await self.svc.same_owner.forward(self.b, m)
        with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok':False,'code':504})):
            self.assertFalse(await self.sends[-1].wait_ready())
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 2)
        with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok':True})):
            self.assertTrue(await self.sends[-1].wait_ready())
        from agentj.same_owner import SameOwner
        self.svc.same_owner = SameOwner(self.svc)
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 2)

    async def test_revoked_pending_never_submits(self):
        await self.confirm()
        m = pmsg('撤销之后不能办理')
        agent, self.host.agent = self.host.agent, None
        self.host.hist_update = lambda *a, **kw: None
        await self.svc.same_owner.forward(self.b, m)
        self.svc.same_owner.consent(self.b, False)
        self.host.agent = agent
        await self.svc.same_owner.retry()
        self.assertFalse(self.sends)


    async def test_stop_waits_and_submit_exception_cannot_execute_old_send(self):
        await self.confirm()
        m = pmsg('停止期间保留')
        updates = []
        self.host.hist_update = lambda *args, **kw: updates.append(kw)
        with patch.object(self.host, 'stopped', return_value=True):
            self.svc._on_pmsg(self.b, m)
            await settle_pending(self.svc)
            self.assertFalse(self.sends)
            self.assertIn('主 Agent 已暂停，恢复后自动办理；无需重发。', updates[-1]['text'])
            self.assertNotIn('未连接', updates[-1]['text'])
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 1)
        m2 = pmsg('入队异常')
        def broken(send):
            self.sends.append(send)
            raise RuntimeError('fixture')
        self.host.agent.submit = broken
        self.assertFalse(await self.svc.same_owner.forward(self.b, m2))
        self.assertEqual(self.sends[-1].state, 'cancelled')
        self.host.agent.submit = self.sends.append
        self.clock.t += 6
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 3)

    async def test_revocation_during_claim_flush_never_delivers(self):
        await self.confirm()
        m = pmsg('flush竞态')
        await self.svc.same_owner.forward(self.b, m)
        async def flush():
            self.svc.same_owner.consent(self.b, False)
            return True
        with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok':True})), patch('agentj.diskq.aflush', flush):
            self.assertFalse(await self.sends[-1].wait_ready())
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'claimed')

    async def test_delivery_boundary_rechecks_after_identity_wait(self):
        from agentj.agent import Agent, Withdrawn
        await self.confirm()
        m = pmsg('身份刷新后撤销')
        await self.svc.same_owner.forward(self.b, m)
        send = self.sends[-1]
        with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok': True})):
            self.assertTrue(await send.wait_ready())
        self.svc.same_owner.consent(self.b, False)
        agent = Agent.__new__(Agent)
        agent.cur_send, agent.host = send, self.host
        write = AsyncMock(return_value=True)
        with self.assertRaises(Withdrawn):
            await agent.deliver(write)
        write.assert_not_awaited()
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'claimed')

    async def test_unknown_reply_latch_never_replays(self):
        from agentj.same_owner import SameOwner
        await self.confirm()
        m = pmsg('回执不明')
        await self.svc.same_owner.forward(self.b, m)
        with self.store._edit('relay-requests.json', {}) as rows:
            rows[self.svc.guards.key(self.b, m['mid'])]['reply_state'] = 'unknown'
        self.svc.same_owner = SameOwner(self.svc)
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 1)
        self.assertFalse(await self.svc.same_owner.forward(self.b, m))
        self.assertFalse(await self.sends[0].wait_ready())

    async def test_forwarded_latch_and_pending_payload_are_one_transaction(self):
        from contextlib import contextmanager
        await self.confirm()
        snapshots = []
        edit = self.store._edit
        @contextmanager
        def audited(name, default):
            with edit(name, default) as rows:
                yield rows
                if name == 'same-owner.json':
                    import copy
                    snapshots.append(copy.deepcopy(rows))
        with patch.object(self.store, '_edit', audited):
            message = pmsg('原子持久交接')
            await self.svc.same_owner.forward(self.b, message)
        for snapshot in snapshots:
            row = snapshot[self.b]
            if message['mid'] in row.get('forwarded', []):
                self.assertIn(message['mid'], row['queue'])
                self.assertEqual(row['queue'][message['mid']]['data']['text'], message['text'])
        self.assertTrue(snapshots)

    async def test_unverified_friend_still_holds_and_expires(self):
        with self.store._edit('friends.json', {}) as rows:
            rows[self.b]['hold_for_owner'] = True
        m = pmsg('普通好友')
        self.svc._on_pmsg(self.b, m)
        await settle_pending(self.svc)
        self.assertFalse(self.sends)
        self.assertTrue(self.svc.asks)
        self.clock.t += self.store.settings()['relay_pending_seconds'] + 1
        self.svc.guards.sweep()
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'expired')

    async def test_claimed_expiry_is_visible_and_never_replayed(self):
        await self.confirm()
        m = pmsg('检查服务这条消息')
        self.svc._on_pmsg(self.b, m)
        await settle_pending(self.svc)
        with patch.object(self.svc.same_owner, 'status', AsyncMock(return_value={'ok':True})):
            self.assertTrue(await self.sends[-1].wait_ready())
        self.clock.t += self.store.settings()['relay_claim_seconds'] + 1
        self.svc.guards.sweep()
        self.assertEqual(self.svc.guards.row(self.b, m['mid'])['state'], 'released')
        self.assertIn(m['mid'], self.host.notices[-1])
        self.assertIn('检查服务', self.host.notices[-1])
        await self.svc.same_owner.retry()
        self.assertEqual(len(self.sends), 1)


from test_p562_acceptance import _Real


class ChannelProjection(_Real):
    async def test_notice_and_avatar_reply_share_real_history_turn(self):
        from agentj import channel_card
        for source, kind in [('wechat','wechat'), ('telegram','telegram'), ('mail','email')]:
            page = channel_card.notice(self.host, source, '原文 '+source, [], '/fixture/message.json')
            channel_card.reply(self.host, source, '回复 '+source, page=page)
            row = self.host.hist.get(page['id'])
            self.assertEqual(row['src']['k'], kind)
            self.assertEqual(row['src']['text'], '原文 '+source)
            self.assertEqual(row['reply']['text'], '回复 '+source)

    async def test_legacy_fallback_redacts_card_but_preserves_model_payload(self):
        from agentj import privacy
        from agentj.wechat import WeChat
        channel = WeChat(self.host)
        self.addAsyncCleanup(channel.stop)
        synthetic = privacy.FAKE_SECRETS['openrouter']
        original = '旧记录原文 key: ' + synthetic
        record = channel.store.put('legacy-fallback', [], original)
        record['state'] = 'pending'
        channel.store.save()
        result = await channel.claim(record['id'], approved=True)
        self.assertTrue(result['ok'])
        row = self.host.hist.get(record['turn'])
        self.assertNotIn(synthetic, row['src']['text'])
        self.assertEqual(row['src']['text'], privacy.owner_text(original))
        self.assertIn(synthetic, self.sends()[-1].text)  # only the card projection is filtered

    async def test_actual_external_avatar_and_handoff_keep_original_card(self):
        from agentj.external_channels import ExternalChannels
        from agentj import provenance
        from unittest.mock import AsyncMock
        channel = ExternalChannels(self.host)
        self.addAsyncCleanup(channel.stop)
        self.host.external_channels = channel
        channel.mirror_paths = AsyncMock(return_value=[])
        channel.assistant = AsyncMock(return_value='分身实际回答')
        text = '原文' * 2500
        await channel.receive_owner('telegram', 'fixture-avatar', text, valid=lambda: True)
        record = next(iter(channel.store.data['records'].values()))
        row = self.host.hist.get(record['page'])
        self.assertEqual(row['src']['text'], text)
        self.assertEqual(row['reply']['text'], '分身实际回答')
        channel.assistant = AsyncMock(return_value=None)
        channel.open_card = lambda _record: None
        from agentj import mail
        mailbox = mail.Mail(self.host)
        cfg = mail.blank_config()
        cfg.update(enabled=True, address='agent@corp.example', allow=[{'addr': 'boss@corp.example', 'role': 'owner'}],
                   imap={'host':'127.0.0.1', 'port':19993, 'security':'plain'},
                   smtp={'host':'127.0.0.1', 'port':19994, 'security':'plain'})
        cfg = mailbox.set_config(cfg)
        channel.assistant = AsyncMock(return_value='邮件分身实际回答')
        await channel.receive_owner('mail', 'fixture-mail-avatar', '邮件分身原文', valid=lambda: True,
                                    admission={'generation': cfg['generation'], 'addr': 'boss@corp.example'})
        mail_avatar = list(channel.store.data['records'].values())[-1]
        mail_page = self.host.hist.get(mail_avatar['page'])
        self.assertEqual(mail_page['src']['k'], 'email')
        self.assertEqual(mail_page['src']['text'], '邮件分身原文')
        self.assertEqual(mail_page['reply']['text'], '邮件分身实际回答')
        channel.assistant = AsyncMock(return_value=None)
        await channel.receive_owner('mail', 'fixture-handoff', '邮件手交原文', valid=lambda: True,
                                    admission={'generation': cfg['generation'], 'addr': 'boss@corp.example'})
        record = list(channel.store.data['records'].values())[-1]
        page_id = record['page']
        result = await channel.claim(record['id'], approved=True)
        self.assertTrue(result['ok'])
        self.assertEqual(record['turn'], page_id)
        row = self.host.hist.get(page_id)
        self.assertEqual(row['src']['k'], 'email')
        self.assertEqual(row['src']['text'], '邮件手交原文')
        send = self.sends()[-1]
        self.answer_turn(send, '手交实际回复')
        self.assertEqual(self.host.hist.get(page_id)['reply']['text'], '手交实际回复')



def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(cls(n) for cls in (Handoff, ChannelProjection)
                              for n in cls.__dict__ if n.startswith('test_'))
