import _hermetic
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from agentj import cloud, notices, wire
from agentj.control import Control
from agentj.control_retirement import Retirement, RECEIPT
from agentj.state import State


class RetirementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name) / 's')
        self.st.init()
        self.link = {'api':'https://agentj.app/api','host_id':wire.b64u(b'h'*16),
                     'tenant':{'slug':'retirementqa','name':'QA'},'linked_at':1,'last_seq':0,'via':'seat'}
        cloud._write_cloud_file(self.st, self.link)
        self.host = SimpleNamespace(st=self.st,sk=self.st.signing_key(),channel=cloud.channel_of(self.st),
            stopping=asyncio.Event(),ws=SimpleNamespace(close=AsyncMock()),relay_up=True,
            adopt_name=lambda _: None,remote_unbind=AsyncMock(return_value='revoked'),seat_unbound=AsyncMock())
        self.now = 100.
        self.order = []
        self.host.credential_revocations = SimpleNamespace(sync=AsyncMock(side_effect=self.credential))
        self.host.remote_pair = SimpleNamespace(sync=AsyncMock(side_effect=self.remote),control_wake=asyncio.Event())
        self.ctl = Control(self.host, post=self.http, clock=lambda:self.now)
        self.host.control = self.ctl
        self.ctl.safety_selected = True
        self.ctl.safety_generation = 2
        self.ctl.safety_deadline = 190
        self.ctl.safety_until = 190
        self.rid = 'r'*22
        self.commits = 0
        self.fail_commit = False
        self.mutate_claim = lambda q:q
        self.server_generation = 2
        self.sk = Ed25519PrivateKey.generate()
        pk = self.sk.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.keypatch = patch('agentj.notices.official_keys',return_value=[pk])
        self.keypatch.start()
        self.addCleanup(self.keypatch.stop)
        self.st.write_private(self.st.root/'control_safety.json',json.dumps(self.ctl._binding(self.link)).encode())
        self.write_epoch()

    def write_epoch(self, epoch=3, ack=3):
        self.st.write_private(self.st.root/'credential_revocations.json',json.dumps(
            {'epoch':epoch,'ack_epoch':ack,'fingerprints':[],'devices':[]}).encode())

    async def credential(self, **kwargs):
        self.assertFalse(self.ctl.safety_ready())
        self.order.append('credential')
        return True

    async def remote(self, **kwargs):
        self.assertFalse(self.ctl.safety_ready())
        self.order.append('remote')

    def signed(self, q):
        body=wire.b64u(json.dumps(q).encode())
        return {'body':body,'sig':wire.b64u(self.sk.sign((RECEIPT+'\n'+body).encode()))}

    def http(self,url,env,**kwargs):
        b=json.loads(wire.unb64u(env['body']))
        if url.split('?')[0].endswith('/sync'):
            self.order.append('legacy_sync')
            return 200, {'unbind':[],'agent_name':None}
        self.assertTrue(url.endswith('/control/retire'))
        self.assertFalse(self.ctl.safety_ready())
        phase=b['phase'];self.order.append(phase)
        obj={'v':1,'nonce':b['nonce'],'host_id':self.link['host_id'],'channel':self.host.channel,
             'retirement_id':self.rid,'generation':self.server_generation,'watermark':8,'epoch':3,'phase':phase,'results':[]}
        if phase=='drain':
            for item in b['items']:
                obj['results'].append({'id':item['id'],'status':200,'headers':{},'body':{'ok':True}})
        if phase=='commit':
            self.commits+=1
            if self.fail_commit: raise cloud.CloudError('network')
            q={'v':1,'host':self.link['host_id'],'channel':self.host.channel,'retirement_id':self.rid,
               'generation':2,'watermark':8,'epoch':3,'nonce':b['nonce'],
               'created_at':int(time.time()*1000),'expires_at':int(time.time()*1000)+30000}
            obj['receipt']=self.signed(self.mutate_claim(q))
        return 200,obj

    async def sync_result(self, *args, **kwargs):
        return cloud.SyncResult('ok')

    async def retire(self):
        with patch('agentj.cloud.send_sync', return_value=cloud.SyncResult('ok')):
            await self.ctl._retire(self.link)

    async def test_committed_receipt_precedes_legacy_polls_and_customer_reopen(self):
        await self.retire()
        self.assertEqual(self.order,['begin','credential','credential','remote','commit','credential','remote'])
        self.assertFalse(self.ctl.safety_selected)
        self.assertTrue(self.ctl.retired)
        self.assertTrue(self.ctl.safety_ready())
        saved=json.loads((self.st.root/'control_safety.json').read_text())
        self.assertEqual(notices.opened(saved['receipt'], RECEIPT)['watermark'],8)

    async def test_disconnect_commit_response_loss_keeps_policy_and_retries(self):
        self.fail_commit=True
        with self.assertRaises(cloud.CloudError): await self.retire()
        self.assertTrue(self.ctl.safety_selected)
        self.assertFalse(self.ctl.safety_ready())
        self.assertFalse(self.ctl.retired)
        self.assertEqual(json.loads((self.st.root/'control_safety.json').read_text()),self.ctl._binding(self.link))
        self.fail_commit=False
        await self.retire()
        self.assertEqual(self.commits,2)
        self.assertTrue(self.ctl.safety_ready())

    async def test_wrong_generation_never_starts_application_or_commit(self):
        self.server_generation=1
        with self.assertRaises(ValueError): await self.retire()
        self.assertEqual(self.order,['begin'])
        self.assertTrue(self.ctl.safety_selected)

    async def test_signed_but_wrong_subject_nonce_watermark_epoch_receipts_refused(self):
        for field,value in [('host','z'*22),('channel','z'*22),('generation',1),('watermark',7),('epoch',2),('nonce','n'*22),('retirement_id','z'*22)]:
            with self.subTest(field=field):
                self.mutate_claim=lambda q,f=field,v=value:{**q,f:v}
                with self.assertRaises(ValueError): await self.retire()
                self.assertTrue(self.ctl.safety_selected)
                self.assertFalse(self.ctl.safety_ready())

    async def test_absent_or_unsigned_receipt_cannot_enable_legacy(self):
        orig=self.ctl.http
        for receipt in (None,{}, {'body':'x','sig':'y'}):
            def broken(url,env,**kw):
                code,obj=orig(url,env,**kw)
                if obj['phase']=='commit':obj['receipt']=receipt
                return code,obj
            self.ctl.http=broken
            with self.assertRaises(ValueError):await self.retire()
            self.assertTrue(self.ctl.safety_selected)

    async def test_final_local_epoch_ack_must_match_before_commit(self):
        self.write_epoch(3,2)
        with self.assertRaisesRegex(ValueError,'epoch'):await self.retire()
        self.assertEqual(self.commits,0)
        self.assertFalse(self.ctl.safety_ready())

    async def test_binding_change_during_drain_stays_closed(self):
        async def remote(**kw):
            cloud._write_cloud_file(self.st,{**self.link,'linked_at':2})
        self.host.remote_pair.sync.side_effect=remote
        with self.assertRaises(ValueError):await self.retire()
        self.assertEqual(self.commits,0)
        self.assertTrue(self.ctl.safety_selected)

    async def test_restart_after_commit_before_persist_repeats_authentication(self):
        orig=self.st.write_private
        def fail(path,data):
            if path.name=='control_safety.json':raise OSError('simulated crash before policy commit')
            return orig(path,data)
        with patch.object(self.st,'write_private',fail):
            with self.assertRaises(OSError):await self.retire()
        restarted=Control(self.host,post=self.http,clock=lambda:self.now)
        self.assertTrue(restarted.safety_selected)
        self.assertFalse(restarted.safety_ready())
        await self.retire()
        self.assertEqual(self.commits,2)

    async def test_restart_after_persist_before_poll_requires_fresh_legacy_round(self):
        with patch('agentj.cloud.send_sync',return_value=cloud.SyncResult('ok')):
            await Retirement(self.ctl,self.link).run()
        self.assertFalse(self.ctl.safety_selected)
        self.assertFalse(self.ctl.safety_ready())
        restarted=Control(self.host,post=self.http,clock=lambda:self.now)
        self.host.control=self.ctl=restarted
        self.assertFalse(restarted.safety_selected)
        self.assertTrue(restarted.handover_pending)
        self.assertFalse(restarted.safety_ready())
        await restarted._legacy_handover()
        self.assertTrue(restarted.safety_ready())

    async def test_failed_first_legacy_poll_retains_fence_and_watchdog_deadline(self):
        self.host.credential_revocations.sync.side_effect=[True,True,False]
        with self.assertRaisesRegex(ValueError,'handover_credential'):await self.retire()
        self.assertFalse(self.ctl.safety_selected)
        self.assertTrue(self.ctl.handover_pending)
        self.assertFalse(self.ctl.safety_ready())
        self.now=190
        task=asyncio.create_task(self.ctl._watchdog())
        await asyncio.sleep(0)
        self.host.stopping.set();await task
        self.host.ws.close.assert_awaited_once()

    async def test_late_exchange_or_metadata_cannot_restore_retired_lease(self):
        await self.retire()
        self.ctl.handover_pending=True
        self.ctl._safety({'generation':2,'lease_seconds':90,'lease_granted':True,'pending':[]},self.now)
        self.assertFalse(self.ctl.safety_ready())
        with self.assertRaisesRegex(ValueError,'retired'):self.ctl._metadata({},'n',self.link)

    async def test_duplicate_withdrawal_is_idempotent_after_success(self):
        await self.retire()
        prior=list(self.order)
        await self.ctl._retire(self.link)
        self.assertEqual(self.order,prior)
        self.assertEqual(self.commits,1)

    async def test_drain_allows_only_original_security_subject_handlers(self):
        r=Retirement(self.ctl,self.link)
        self.ctl.retiring=True
        r.request('begin')
        for kind in ('credential-revoke','remote-pair','sync'):
            status,obj=r.post(self.link['api']+'/v1/host/'+kind,{'original':'envelope'})
            self.assertEqual(status,200)
        for kind in ('report','seat-leave','retire'):
            with self.assertRaises(ValueError):r.post(self.link['api']+'/v1/host/'+kind,{})

    async def test_timed_out_receipt_does_not_extend_safety(self):
        orig=self.ctl.http
        def slow(url,env,**kw):
            code,obj=orig(url,env,**kw)
            if obj['phase']=='commit':self.now+=12
            return code,obj
        self.ctl.http=slow
        with self.assertRaises(ValueError):await self.retire()
        self.assertFalse(self.ctl.safety_ready())
        self.assertTrue(self.ctl.safety_selected)

    async def test_malformed_persisted_policy_stays_closed(self):
        for saved in ([],{}, {'binding':self.ctl._binding(self.link),'generation':2,'receipt':None,'extra':True},
                      {'binding':self.ctl._binding(self.link),'generation':2,'receipt':{'body':'x','sig':'y'}}):
            self.st.write_private(self.st.root/'control_safety.json',json.dumps(saved).encode())
            ctl=Control(self.host)
            self.assertTrue(ctl.safety_selected)
            self.assertFalse(ctl.safety_ready())

    async def test_false_native_application_result_cannot_commit(self):
        self.host.credential_revocations.sync.side_effect=None
        self.host.credential_revocations.sync.return_value=False
        with self.assertRaisesRegex(ValueError,'retirement_credential'):await self.retire()
        self.assertEqual(self.commits,0)
        self.assertFalse(self.ctl.safety_ready())

    async def test_retired_native_calls_use_routing_hint_without_control_transport(self):
        await self.retire()
        calls=[]
        self.ctl.http=lambda url,env,**kw:(calls.append((url, kw.get('control_retired'))) or (200,{}))
        for path in ('credential-revoke','remote-pair','sync','report'):
            self.assertEqual(self.ctl.post(self.link['api']+'/v1/host/'+path,{}),(200,{}))
        self.assertEqual(calls,[(self.link['api']+'/v1/host/'+p, True)
            for p in ('credential-revoke','remote-pair','sync','report')])

    async def test_adopted_generation_survives_host_restart_without_lease(self):
        self.ctl._safety({'generation':3,'lease_seconds':90,'lease_granted':False,'pending':[]},self.now)
        restarted=Control(self.host)
        self.assertTrue(restarted.safety_selected)
        self.assertEqual(restarted.safety_generation,3)
        self.assertFalse(restarted.safety_ready())

    async def test_missing_current_binding_cannot_complete_handover_without_polls(self):
        with patch('agentj.cloud.send_sync', return_value=cloud.SyncResult('ok')):
            await Retirement(self.ctl, self.link).run()
        self.host.credential_revocations.sync.reset_mock()
        self.host.remote_pair.sync.reset_mock()
        cloud.delete_cloud(self.st)
        with self.assertRaisesRegex(ValueError, 'control_handover_binding'):
            await self.ctl._legacy_handover()
        self.host.credential_revocations.sync.assert_not_awaited()
        self.host.remote_pair.sync.assert_not_awaited()
        self.assertTrue(self.ctl.handover_pending)
        self.assertFalse(self.ctl.safety_ready())
        self.assertIsNone(self.ctl.safety_apply_task)

    async def test_mismatched_persisted_binding_cannot_complete_handover_without_polls(self):
        with patch('agentj.cloud.send_sync', return_value=cloud.SyncResult('ok')):
            await Retirement(self.ctl, self.link).run()
        self.host.credential_revocations.sync.reset_mock()
        self.host.remote_pair.sync.reset_mock()
        path = self.st.root / 'control_safety.json'
        saved = json.loads(path.read_text())
        saved['binding']['linked_at'] = 2
        self.st.write_private(path, json.dumps(saved).encode())
        with self.assertRaisesRegex(ValueError, 'control_handover_binding'):
            await self.ctl._legacy_handover()
        self.host.credential_revocations.sync.assert_not_awaited()
        self.host.remote_pair.sync.assert_not_awaited()
        self.assertTrue(self.ctl.handover_pending)
        self.assertFalse(self.ctl.safety_ready())
        self.assertIsNone(self.ctl.safety_apply_task)

    async def test_failed_retired_handover_retries_within_four_seconds(self):
        self.ctl.retired = True
        self.ctl.safety_selected = False
        self.ctl.handover_pending = True
        self.ctl.failures = 12
        self.ctl.jitter = lambda: 1
        async def fail():
            self.host.stopping.set()
            raise ValueError('simulated first legacy poll failure')
        self.ctl._legacy_handover = fail
        await self.ctl.run()
        self.assertLessEqual(self.ctl.retry_at - self.now, 4)
        self.assertFalse(self.ctl.safety_ready())

    async def test_retirement_preserves_native_64k_response_limit(self):
        calls = []
        original = self.ctl.http
        def transport(url, env, **kwargs):
            calls.append(kwargs.get('max_response'))
            return original(url, env, **kwargs)
        self.ctl.http = transport
        await self.retire()
        self.assertEqual(calls, [64 * 1024, 64 * 1024])

    async def test_retired_transport_preserves_each_native_response_bound(self):
        await self.retire()
        calls = []
        self.ctl.http = lambda url, env, **kwargs: (calls.append((url, kwargs)) or (200, {}))
        self.ctl.post(self.link['api'] + '/v1/host/report', {})
        self.ctl.post(self.link['api'] + '/v1/host/credential-revoke', {}, max_response=64 * 1024)
        self.ctl.post(self.link['api'] + '/v1/host/remote-pair', {})
        self.assertEqual([kw.get('max_response', cloud.MAX_RESPONSE) for _, kw in calls],
                         [128 * 1024, 64 * 1024, cloud.MAX_RESPONSE])
        self.assertTrue(all(kw['control_retired'] is True for _, kw in calls))


class RetirementNativeHttp(unittest.TestCase):
    def test_actual_http_uses_fixed_header_without_relaxing_url_policy(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                seen.append((self.path, self.headers.get('X-AgentJ-Control-Retired')))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for path in ('credential-revoke', 'remote-pair', 'sync', 'report'):
                self.assertEqual(cloud.post_json(base + '/api/v1/host/' + path, {}, control_retired=True)[0], 200)
            cloud.post_json(base + '/api/v1/host/sync', {})
            self.assertEqual(seen, [('/api/v1/host/' + p, '1') for p in ('credential-revoke', 'remote-pair', 'sync', 'report')]
                + [('/api/v1/host/sync', None)])
            for suffix in ('/api/v1/host/seat-leave', '/api/v1/host/control/retire', '/api/v1/host/sync?control_retired=1', '/api/v1/host/sync#fragment'):
                with self.subTest(suffix=suffix), self.assertRaises(cloud.CloudError):
                    cloud.post_json(base + suffix, {}, control_retired=True)
            self.assertEqual(len(seen), 5)  # rejected before network
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
