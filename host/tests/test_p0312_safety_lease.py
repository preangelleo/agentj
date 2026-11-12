import _hermetic
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agentj import cloud, wire, serve, remote_pair
from agentj.control import Control
from agentj.state import State


class SafetyLease(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 'state')
        self.st.init()
        self.link = {'api': 'https://agentj.app/api', 'host_id': wire.b64u(b'h'*16),
                     'tenant': {'slug': 'safetyqa', 'name': 'QA'}, 'linked_at': 1, 'last_seq': 0, 'via': 'seat'}
        cloud._write_cloud_file(self.st, self.link)
        self.host = SimpleNamespace(st=self.st, sk=self.st.signing_key(), channel=cloud.channel_of(self.st),
                                    stopping=asyncio.Event(), ws=SimpleNamespace(close=AsyncMock()), relay_up=True)
        self.now = 100.
        self.control = Control(self.host, clock=lambda: self.now)
        self.host.control = self.control
        self.control.safety_selected = True
        self.control.safety_applied_generation = 1

    def safety(self, **kw):
        return {'generation': 1, 'lease_seconds': 90, 'lease_granted': True, 'pending': [], **kw}

    def metadata(self):
        return {'v': 1, 'transport': 'websocket', 'host_id': self.link['host_id'], 'channel': self.host.channel,
                'connection_id': 'c'*22, 'client_nonce': 'nonce', 'heartbeat_seconds': 60, 'lease_seconds': 90,
                'safety_poll_required': False, 'safety_protocol': 1,
                'ws_path': '/v1/host/control/ws', 'exchange_path': '/v1/host/control/exchange'}

    async def test_handshake_selects_without_grant_and_restart_retains_policy(self):
        self.control._metadata(self.metadata(), 'nonce', self.link)
        self.assertFalse(self.control.safety_ready())
        restarted = Control(self.host, clock=lambda: self.now)
        self.assertTrue(restarted.safety_selected)
        self.assertFalse(restarted.safety_ready())
        with self.assertRaises(ValueError):
            restarted._metadata({**self.metadata(), 'safety_poll_required': True}, 'nonce', self.link)

    async def test_empty_outbox_cannot_skip_first_local_signed_snapshot(self):
        self.control.safety_applied_generation = 0
        self.control._safety(self.safety(), self.now)
        self.assertFalse(self.control.safety_ready())
        self.assertTrue(self.control.invalidations.is_set())

    async def test_new_binding_does_not_inherit_persisted_mode(self):
        self.control._metadata(self.metadata(), 'nonce', self.link)
        cloud._write_cloud_file(self.st, {**self.link, 'linked_at': 2})
        self.assertFalse(Control(self.host).safety_selected)

    async def test_expiry_uses_request_start_not_response_arrival(self):
        self.control._safety(self.safety(), 10.)
        self.assertFalse(self.control.safety_ready())
        with self.assertRaises(ValueError): self.control.require_safety()
        self.control._safety(self.safety(), self.now)
        self.assertTrue(self.control.safety_ready())
        self.now = 190
        self.assertFalse(self.control.safety_ready())

    async def test_entitlement_boundary_shortens_lease(self):
        self.control._safety(self.safety(lease_seconds=1), self.now)
        self.assertTrue(self.control.safety_ready())
        self.now += 1
        self.assertFalse(self.control.safety_ready())

    async def test_pending_fences_and_cannot_combine_with_lease(self):
        row = {'generation': 1, 'seq': 9}
        self.control._safety(self.safety(lease_granted=False, pending=[row]), self.now)
        self.assertFalse(self.control.safety_ready())
        self.assertEqual(self.control.safety_pending, {(1, 9)})
        self.assertTrue(self.control.invalidations.is_set())
        with self.assertRaises(ValueError): self.control._safety(self.safety(pending=[row]), self.now)

    async def test_regression_wrong_generation_duplicate_and_unbounded_pending_refused(self):
        self.control._safety(self.safety(generation=2), self.now)
        row = {'generation': 2, 'seq': 1}
        for value in (self.safety(), self.safety(generation=2, pending=[{'generation': 1, 'seq': 1}]),
                      self.safety(generation=2, lease_granted=False, pending=[row, row]),
                      self.safety(generation=2, lease_granted=False, pending=[{'generation': 2, 'seq': i+1} for i in range(33)]),
                      self.safety(lease_seconds=91), self.safety(lease_seconds=True), self.safety(lease_seconds=0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.control._safety(value, self.now)

    async def test_expiry_watchdog_closes_live_relay(self):
        task = asyncio.create_task(self.control._watchdog())
        await asyncio.sleep(0)
        self.host.stopping.set()
        await task
        self.host.ws.close.assert_awaited_once()
        self.assertFalse(self.host.relay_up)

    async def test_legacy_watchdog_does_not_close_relay(self):
        self.control.safety_selected = False
        task = asyncio.create_task(self.control._watchdog())
        await asyncio.sleep(0)
        self.host.stopping.set()
        await task
        self.host.ws.close.assert_not_awaited()

    async def test_selected_disconnected_security_requests_never_fallback_to_poll(self):
        for path in ('credential-revoke', 'remote-pair'):
            self.assertEqual(self.control.post(self.link['api']+'/v1/host/'+path, {}), (503, {}))

    async def test_expired_frame_refuses_before_any_dispatch(self):
        host = SimpleNamespace(control=self.control)
        self.assertIsNone(await serve.Host.on_frame(host, b'\x11\x00\x00\x00\x01'))

    async def test_unsolicited_hint_cannot_renew_lease(self):
        class Socket:
            def __aiter__(self): return self
            async def __anext__(self):
                if getattr(self, 'sent', False): raise StopAsyncIteration
                self.sent = True
                return json.dumps({'v': 1, 'type': 'invalidate', 'streams': ['credential_revoke']})
        self.control._safety(self.safety(), self.now)
        await self.control._reader(Socket())
        self.assertFalse(self.control.safety_ready())
        self.assertEqual(self.control.safety_fence, 1)

    async def test_exchange_started_before_hint_cannot_renew_lease(self):
        ctl = self.control
        ctl.connection, ctl.lease_until = 'c'*22, 190
        async def send(raw):
            inner = json.loads(wire.unb64u(json.loads(raw)['body']))
            ctl.safety_fence += 1
            ctl.pending[inner['nonce']].set_result({'v': 1, 'connection_id': ctl.connection, 'nonce': inner['nonce'],
                'results': [], 'committed': [], 'safety': self.safety()})
        ctl.ws = SimpleNamespace(send=send)
        await ctl._exchange([])
        self.assertFalse(ctl.safety_ready())
        self.assertEqual(ctl.safety_deadline, 0)

    async def test_outbox_ack_is_after_original_effect_and_applied_ack_drain(self):
        ctl = self.control
        ctl.safety_generation = 1
        ctl.safety_applied_generation = 0
        ctl.safety_pending = {(1, 3), (1, 5)}
        order = []
        async def exchange(items, safety_ack=None):
            if safety_ack is not None: order.append(('ack', safety_ack))
            return []
        ctl._exchange = exchange
        async def credential(): order.append('credential')
        async def remote(): order.append('remote_pair')
        async def unbind(rid, did): order.append(('apply', rid)); return 'revoked'
        self.host.credential_revocations = SimpleNamespace(sync=credential)
        self.host.remote_pair = SimpleNamespace(sync=remote)
        self.host.adopt_name = lambda name: None
        self.host.remote_unbind = unbind
        self.host.seat_unbound = AsyncMock()
        replies = [cloud.SyncResult('ok', '', [('one','dev')]), cloud.SyncResult('ok')]
        def send_sync(st, results, **kw):
            order.append(('sync', list(results)))
            return replies.pop(0)
        ctl.invalidations.set()
        with patch('agentj.cloud.send_sync', send_sync):
            task = asyncio.create_task(ctl._pushes())
            for _ in range(100):
                await asyncio.sleep(.001)
                if any(isinstance(v, tuple) and v[0] == 'ack' for v in order): break
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(order, ['credential','credential',('sync', []),('apply','one'),
            ('sync',[('one','revoked')]),'remote_pair',('ack',[{'generation':1,'seq':3},{'generation':1,'seq':5}])])

    async def test_failed_application_keeps_outbox_unacknowledged(self):
        ctl = self.control
        ctl.safety_generation = 1
        ctl.safety_pending = {(1, 1)}
        calls = []
        async def exchange(items, safety_ack=None): calls.append(safety_ack)
        ctl._exchange = exchange
        self.host.credential_revocations = SimpleNamespace(sync=AsyncMock(side_effect=ValueError('signature')))
        ctl.invalidations.set()
        task = asyncio.create_task(ctl._pushes())
        for _ in range(20): await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(calls, [None])
        self.assertFalse(ctl.safety_ready())
        self.assertEqual(ctl.safety_pending, {(1,1)})

    async def test_pending_preserves_prior_hard_deadline_without_extending_it(self):
        self.control._safety(self.safety(), self.now)
        self.assertEqual(self.control.safety_deadline, 190)
        self.now = 150
        self.control._safety(self.safety(lease_granted=False, pending=[{'generation':1,'seq':4}]), self.now)
        self.assertFalse(self.control.safety_ready())
        self.assertEqual(self.control.safety_deadline, 190)
        task = asyncio.create_task(self.control._watchdog())
        await asyncio.sleep(0)
        self.host.ws.close.assert_not_awaited()
        self.now = 190
        await asyncio.sleep(.3)
        self.host.stopping.set()
        await task
        self.host.ws.close.assert_awaited_once()

    async def test_unrelated_pair_grant_is_refused_while_fenced(self):
        result = await serve.Host._grant_pair(self.host, SimpleNamespace())
        self.assertEqual(result, {'ev':'denied','reason':'control_safety_expired'})

    async def test_remote_queue_must_have_an_applied_reply_before_ack(self):
        rp = remote_pair.RemotePair(self.host)
        rp.poll = lambda *args: (200, {'now':0, 'requests':[{'fake':'request'}]})
        rp.execute = AsyncMock()
        with patch('agentj.remote_pair.notices.opened', return_value={'id':'one'}):
            with self.assertRaisesRegex(ValueError, 'remote_pair_unapplied'):
                await rp.sync()
        rp.execute.assert_awaited_once()

    async def test_remote_queue_drains_all_pages_before_returning(self):
        rp = remote_pair.RemotePair(self.host)
        pages = [
            {'requests':[{'id':'one'}]}, {'requests':[{'id':'two'}]},
            {'requests':[{'id':'two'}]}, {'requests':[]}, {'requests':[]}]
        sent = []
        def poll(link, results):
            sent.append(results)
            return 200, pages.pop(0)
        rp.poll = poll
        async def execute(env):
            rp.results[env['id']] = ({'id':env['id'], 'expires_at':1000}, {'ciphertext':env['id']})
        rp.execute = execute
        with patch('agentj.remote_pair.notices.opened', side_effect=lambda env, ctx: env): await rp.sync()
        self.assertEqual(len(sent), 5)
        self.assertEqual(sent[1][0]['id'], 'one')
        self.assertEqual(sent[3][0]['id'], 'two')

    async def test_remote_backlog_bound_never_silently_claims_drained(self):
        rp = remote_pair.RemotePair(self.host)
        rp.poll = lambda *args: (200, {'requests':[{'id':'one'}]})
        async def execute(env): rp.results['one'] = ({'expires_at':1000}, {'ciphertext':'one'})
        rp.execute = execute
        with patch('agentj.remote_pair.notices.opened', return_value={'id':'one'}):
            with self.assertRaisesRegex(ValueError, 'remote_pair_pending'): await rp.sync()

    async def test_revocation_background_poll_skips_selected_hosts(self):
        from agentj.credential_revoke import Revocations
        rev = Revocations(self.host)
        rev.sync = AsyncMock()
        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError): await rev.run()
        rev.sync.assert_not_awaited()
        self.control.safety_selected = False
        with patch('asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError): await rev.run()
        rev.sync.assert_awaited_once()

    async def test_selected_remote_metadata_changes_push_without_repeating_four_second_poll(self):
        self.control._safety(self.safety(), self.now)
        self.host.pairing = None
        rp = remote_pair.RemotePair(self.host)
        rp.sync = AsyncMock()
        ticks = 0
        async def sleep(seconds):
            nonlocal ticks
            ticks += 1
            if ticks == 2: self.host.relay_up = False
            if ticks == 4: raise asyncio.CancelledError
        with patch('asyncio.sleep', sleep):
            with self.assertRaises(asyncio.CancelledError): await rp.run()
        self.assertEqual(rp.sync.await_count, 2)  # initial projection and actual up→down, no polling

    async def test_safety_auth_rejection_immediately_fences(self):
        self.control._safety(self.safety(), self.now)
        class Socket:
            def __aiter__(self): return self
            async def __anext__(self):
                return json.dumps({'v':1,'type':'control_error','status':403})
        with self.assertRaises(cloud.CloudError): await self.control._reader(Socket())
        self.assertFalse(self.control.safety_ready())

    async def test_withdrawn_cohort_404_does_not_downgrade_known_binding(self):
        self.control.http = lambda *args, **kwargs: (404, {})
        await self.control.session(self.link)
        self.assertTrue(self.control.safety_selected)
        self.assertFalse(self.control.safety_ready())
        self.assertEqual(self.control.post(self.link['api']+'/v1/host/credential-revoke', {}), (503, {}))
