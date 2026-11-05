"""One-way external admission, native card boundary, durable non-replay."""
import _hermetic  # noqa
import asyncio
import json
import pathlib
import secrets
import tempfile
import types
import unittest
from unittest import mock
from agentj import hooks, hook_service, external_mcp, provenance


class MCP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent, self.pickup, self.asks = [], [], []
        self.host = types.SimpleNamespace(st=types.SimpleNamespace(root=pathlib.Path(self.tmp.name), log=lambda *a, **k: None),
            stopped=lambda: False, hist_add=lambda *a: {'id': 1}, peer_broadcast=lambda *a: None,
            agent=types.SimpleNamespace(submit=self.sent.append), ask=self.ask,
            elevate=types.SimpleNamespace(out=types.SimpleNamespace(send=self.offer)))
        self.service = hook_service.HookService(self.host)
        self.service.ensure_net = lambda: None
        self.mcp = self.service.mcp
        result = await self.mcp.manage('mcp-create', {'name': 'assistant', 'origins': ['https://client.example']})
        self.assertTrue(result['ok'])
        self.cid = next(iter(self.mcp.doc['connectors']))
        self.token = self.pickup[-1]['value']
        self.meta = {'h': self.cid, 'm': 'POST', 'hd': {'authorization': 'Bearer ' + self.token,
            'origin': 'https://client.example', 'mcp-protocol-version': external_mcp.REVISION}}

    async def ask(self, tool, obj, **kw):
        self.asks.append((tool, obj, kw))
        return {'behavior': 'allow', 'grant_info': {'device': 'paired-test'}}

    async def offer(self, req):
        self.pickup.append(req)
        return {'result': 'sent'}

    async def rpc(self, method, params=None, **changes):
        meta = {**self.meta, **changes}
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}).encode()
        return await self.service.handle(meta, body)

    def args(self, text='hello', key='job-1'):
        return {'name': 'agentj_request', 'arguments': {'text': text, 'idempotency_key': key}}

    async def test_auth_list_initialize_no_notice_and_hash_only(self):
        for method, params in [('initialize', {'protocolVersion': external_mcp.REVISION}), ('tools/list', {}), ('ping', {})]:
            status, obj = await self.rpc(method, params)
            self.assertEqual((status, obj), (200, {'mcp': 1, 'status': 'authenticated'}))
        self.assertEqual(self.sent, [])
        self.assertNotIn(self.token, self.mcp.path.read_text())
        self.assertNotIn(self.token, str(self.mcp.views()))
        ids = json.loads(self.service.ids_text()); self.assertIn(self.cid, ids['mcp']); self.assertIn(self.cid, ids['h'])
        self.assertEqual(self.mcp.path.stat().st_mode & 0o777, 0o600)

    async def test_accepted_external_source_and_restart_dedupe(self):
        status, obj = await self.rpc('tools/call', self.args())
        self.assertEqual((status, obj['status']), (200, 'accepted'))
        self.assertEqual(len(self.sent), 1)
        source = self.sent[0].source
        self.assertEqual(provenance.normalize(source), {'kind': 'external_mcp', 'role': 'group', 'connector': self.cid})
        self.assertFalse(provenance.is_owner(source)); self.assertIsNone(provenance.owner_channel(source))
        self.service.mcp = external_mcp.Service(self.service)
        _, dup = await self.rpc('tools/call', self.args())
        self.assertEqual(dup, {'mcp': 1, 'status': 'duplicate', 'delivery': obj['delivery']})
        self.assertEqual((await self.rpc('tools/call', self.args('changed')))[0], 409)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn('hello', self.mcp.path.read_text())

    async def test_bad_auth_origin_revision_method_shape(self):
        for hd in ({}, {'authorization': 'Bearer wrong'}, {**self.meta['hd'], 'origin': 'https://evil.example'},
                   {**self.meta['hd'], 'mcp-protocol-version': 'invalid'}):
            self.assertGreaterEqual((await self.rpc('tools/list', hd=hd))[0], 400)
        self.assertEqual((await self.rpc('tools/list', m='GET'))[0], 405)
        for body in (b'[]', b'{}', b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
                     json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': self.args() | {'source': 'owner'}}).encode()):
            self.assertEqual((await self.service.handle(self.meta, body))[0], 400)
        self.assertEqual(self.sent, [])

    async def test_rotate_revoke_and_generic_hook_cannot_change(self):
        self.assertEqual((await self.service.cli({'t': 'rotate', 'id': self.cid})), {'ok': False, 'why': 'phone_only'})
        self.assertTrue((await self.mcp.manage('mcp-rotate', {'id': self.cid}))['ok'])
        self.assertEqual((await self.rpc('tools/list'))[0], 401)
        self.meta['hd']['authorization'] = 'Bearer ' + self.pickup[-1]['value']
        self.assertEqual((await self.rpc('tools/list'))[0], 200)
        self.assertTrue((await self.mcp.manage('mcp-revoke', {'id': self.cid}))['ok'])
        self.assertEqual((await self.rpc('tools/list'))[0], 410)
        self.assertNotIn(self.cid, self.mcp.ids())
        self.assertEqual([a[1]['mcp'] for a in self.asks], ['mcp-create', 'mcp-rotate', 'mcp-revoke'])
        self.assertTrue(all(a[2]['batch'] is False for a in self.asks))

    async def test_no_signed_device_or_pickup_means_inactive(self):
        with mock.patch.object(self.host, 'ask', new=mock.AsyncMock(return_value={'behavior': 'allow'})):
            self.assertEqual((await self.mcp.manage('mcp-create', {'name': 'denied'}))['why'], 'denied')
        with mock.patch.object(self.host.elevate.out, 'send', new=mock.AsyncMock(return_value={'result': 'no_device'})):
            self.assertEqual((await self.mcp.manage('mcp-rotate', {'id': self.cid}))['why'], 'pickup_failed')
        self.assertEqual((await self.rpc('tools/list'))[0], 410)

    async def test_concurrent_dedupe_uncertain_submit_no_replay(self):
        results = await asyncio.gather(*[self.rpc('tools/call', self.args()) for _ in range(8)])
        self.assertEqual(sum(obj['status'] == 'accepted' for _, obj in results), 1)
        self.assertEqual(len(self.sent), 1)
        with mock.patch.object(self.host.agent, 'submit', side_effect=RuntimeError('private result must not leak')):
            status, obj = await self.rpc('tools/call', self.args(key='uncertain'))
        self.assertEqual(status, 503); self.assertNotIn('private', str(obj))
        self.assertEqual((await self.rpc('tools/call', self.args(key='uncertain')))[0], 503)
        self.assertEqual(len(self.sent), 1)

    async def test_real_grant_info_contract_never_accepts_legacy_alias(self):
        # Host.ask returns grant_info, not grant. A wrong substitute must fail.
        self.assertEqual(self.asks[0][0], 'AgentJHook')
        with mock.patch.object(self.host, 'ask', new=mock.AsyncMock(return_value={
                'behavior': 'allow', 'grant': {'device': 'not-the-real-contract'}})):
            result = await self.mcp.manage('mcp-create', {'name': 'wrong-contract'})
        self.assertEqual(result, {'ok': False, 'why': 'denied'})
        self.assertEqual(len(self.pickup), 1)

    async def test_pickup_exception_revokes_persisted_connector(self):
        with mock.patch.object(self.host.elevate.out, 'send', new=mock.AsyncMock(side_effect=OSError('synthetic'))):
            with self.assertRaises(OSError):
                await self.mcp.manage('mcp-rotate', {'id': self.cid})
        reopened = external_mcp.Service(self.service)
        self.assertEqual(reopened.get(self.cid)['state'], 'revoked')
        self.assertNotIn('hash', reopened.get(self.cid))
        self.assertNotIn(self.cid, reopened.ids())
        self.assertEqual((await self.rpc('tools/list'))[0], 410)

    async def test_pickup_cancel_revokes_persisted_connector(self):
        with mock.patch.object(self.host.elevate.out, 'send', new=mock.AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.mcp.manage('mcp-rotate', {'id': self.cid})
        reopened = external_mcp.Service(self.service)
        self.assertEqual(reopened.get(self.cid)['state'], 'revoked')
        self.assertNotIn('hash', reopened.get(self.cid))
        self.assertEqual((await self.rpc('tools/list'))[0], 410)

    async def test_waiting_admission_rechecks_stop_pause_and_hook_state(self):
        for state in ('stop', 'pause', 'revoked'):
            with self.subTest(state=state):
                h = self.service.store.get(self.cid)
                h['state'], h['enabled'] = 'active', True
                self.host.stopped = lambda: False
                await self.mcp.lock.acquire()
                task = asyncio.create_task(self.rpc('tools/call', self.args(key='race-' + state)))
                try:
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                    if state == 'stop':
                        self.host.stopped = lambda: True
                    elif state == 'pause':
                        h['enabled'] = False
                    else:
                        h['state'] = 'revoked'
                finally:
                    self.mcp.lock.release()
                self.assertEqual((await task)[0], 423)
                self.assertEqual(self.sent, [])
                self.assertEqual(self.mcp.doc['receipts'], {})

    async def test_stop_quota_and_spoofed_mcp_header_do_not_deliver(self):
        self.host.stopped = lambda: True
        self.assertEqual((await self.rpc('tools/call', self.args()))[0], 423)
        self.host.stopped = lambda: False
        with mock.patch.object(self.service.store, 'take_day', return_value=False):
            self.assertEqual((await self.rpc('tools/call', self.args()))[0], 429)
        self.assertEqual(self.sent, [])
        self.assertEqual((await self.rpc('tools/list', h=hooks.new_id()))[0], 404)


class RealSignedMCP(unittest.IsolatedAsyncioTestCase):
    async def test_signed_host_ask_to_real_once_pickup(self):
        with tempfile.TemporaryDirectory() as td:
            from test_l1 import _owner_host, _state, Phone, _ready
            from test_p73_secret import fa_out
            sent=[];st=_state(td);host=_owner_host(st,sent);ph=Phone(st,with_passkey=True);s=_ready(host,ph)
            service=hook_service.HookService(host);service.ensure_net=lambda:None
            task=asyncio.create_task(service.mcp.manage('mcp-create',{'name':'synthetic','origins':['https://client.example']}))
            try:
                for _ in range(200):
                    cards=[o for _,o in sent if o.get('t')=='ask']
                    if cards or task.done():break
                    await asyncio.sleep(.01)
                self.assertTrue(cards,'real Host.ask must display a device card')
                card=cards[-1];self.assertIn('mcp-create',card['summary']);self.assertIn('request-only',card['summary'])
                wrong=ph.answer(card,True,summary=card['summary']+' changed')
                await host._app(s,wrong);await asyncio.sleep(.02);self.assertFalse(task.done(),'changed-summary signature must not activate')
                await host._app(s,ph.answer(card,True))
                result=await asyncio.wait_for(task,3);self.assertTrue(result['ok'],result)
                pickup=[o for _,o in sent if o.get('t')=='secret_out'][-1]
                self.assertFalse(any(o.get('t')=='secret_out_val' for _,o in sent))
                await host._app(s,{'t':'secret_out_open','id':pickup['id'],'n':pickup['n']})
                self.assertFalse(any(o.get('t')=='secret_out_val' for _,o in sent))
                await host._app(s,{'t':'secret_out_open','id':pickup['id'],'n':pickup['n'],'fa':fa_out(ph.auth,ph,pickup)})
                vals=[o for _,o in sent if o.get('t')=='secret_out_val'];self.assertEqual(len(vals),1)
                token=vals[0]['value'];cid=next(iter(service.mcp.doc['connectors']))
                self.assertNotIn(token,service.mcp.path.read_text())
                meta={'h':cid,'m':'POST','hd':{'authorization':'Bearer '+token,'mcp-protocol-version':external_mcp.REVISION}}
                body=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'}).encode()
                self.assertEqual((await service.handle(meta,body))[0],200)
                await host._app(s,{'t':'secret_out_open','id':pickup['id'],'n':pickup['n'],'fa':fa_out(ph.auth,ph,pickup)})
                self.assertEqual(sum(o.get('t')=='secret_out_val' for _,o in sent),1)
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
                host.elevate.out.cancel_all('stopped') if hasattr(host.elevate.out,'cancel_all') else None
