import _hermetic
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from agentj import cloud, wire, serve
from agentj.control import Control, HEARTBEAT, LEASE
from agentj.state import State


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = State(Path(self.tmp.name)/'s')
        self.st.init()
        self.link = {'api':'https://agentj.app/api', 'host_id':wire.b64u(b'h'*16),
                     'tenant':{'slug':'controlqa','name':'QA'}, 'linked_at':1, 'last_seq':0, 'via':'code'}
        cloud._write_cloud_file(self.st, self.link)
        self.host = SimpleNamespace(st=self.st, sk=self.st.signing_key(), channel=cloud.channel_of(self.st),
                                    stopping=asyncio.Event(), credential_revocations=SimpleNamespace(sync=AsyncMock()),
                                    control_sync_wake=asyncio.Event(), remote_pair=SimpleNamespace(control_wake=asyncio.Event()))
        self.posts = []
        self.control = Control(self.host, post=self.legacy, clock=lambda: 100, jitter=lambda:.5)
        self.control.loop = asyncio.get_running_loop()
        self.control.base = self.link['api']
        self.seq = 0

    def legacy(self, url, env, **kwargs):
        self.posts.append((url, env))
        inner = json.loads(wire.unb64u(env['body']))
        if url.endswith('/sync'): return 200, {'unbind':[], 'agent_name':None}
        if url.endswith('/report'):
            if inner['seq'] <= self.seq: return 409, {'error':'replay'}
            self.seq = inner['seq']
            return 200, {'ok':True}
        raise AssertionError(url)

    def ready(self, *, lose=False):
        ctl = self.control
        ctl.connection = 'c'*22
        ctl.lease_until = 190
        async def send(raw):
            env = json.loads(raw)
            inner = json.loads(wire.unb64u(env['body']))
            rows = []
            for item in inner['items']:
                status, body = self.legacy(self.link['api']+'/v1/host/'+item['kind'], item['envelope'])
                rows.append({'id':item['id'], 'status':status, 'headers':{}, 'body':body})
            if lose: raise ConnectionError('synthetic disconnect after commit')
            ctl.pending[inner['nonce']].set_result({'v':1,'connection_id':ctl.connection,'nonce':inner['nonce'],
                'results':rows,'committed':[r['id'] for r in rows if r['status'] == 200]})
        ctl.ws = SimpleNamespace(send=send)

    def metadata(self, nonce):
        return {'v':1,'transport':'websocket','host_id':self.link['host_id'],'channel':self.host.channel,
                'connection_id':'c'*22,'client_nonce':nonce,'heartbeat_seconds':60,'lease_seconds':90,
                'safety_poll_required':True,'ws_path':'/v1/host/control/ws', 'exchange_path':'/v1/host/control/exchange'}

    async def test_real_sync_adapter_and_disconnect_fall_back_without_failure(self):
        self.ready()
        result = await asyncio.to_thread(lambda: cloud.send_sync(self.st, [], post=self.control.post))
        self.assertEqual(result.kind, 'ok')
        self.ready(lose=True)
        result = await asyncio.to_thread(lambda: cloud.send_sync(self.st, [], post=self.control.post))
        self.assertEqual(result.kind, 'ok')
        self.assertFalse(self.control.usable())
        self.assertGreater(self.control.retry_at, 100)
        result = await asyncio.to_thread(lambda: cloud.send_sync(self.st, [], post=self.control.post))
        self.assertEqual(result.kind, 'ok')

    async def test_real_report_commit_response_lost_uses_existing_replay_recovery(self):
        self.ready(lose=True)
        result = await asyncio.to_thread(lambda: cloud.send_report(self.st, set(), {}, post=self.control.post))
        self.assertEqual(result.kind, 'ok')
        seqs = [json.loads(wire.unb64u(e['body']))['seq'] for u,e in self.posts]
        self.assertEqual(len(seqs),3)  # DO committed; same envelope legacy409; existing fresh-seq retry200
        self.assertEqual(seqs[0],seqs[1])
        self.assertGreater(seqs[2],seqs[1])

    async def test_no_control_metadata_means_exact_legacy_inner_request(self):
        result = await asyncio.to_thread(lambda: cloud.send_sync(self.st, [], post=self.control.post))
        self.assertEqual(result.kind,'ok')
        self.assertEqual(len(self.posts),1)
        body = json.loads(wire.unb64u(self.posts[0][1]['body']))
        self.assertEqual(set(body), {'v','t','channel','ts','results'})
        self.assertEqual(body['t'],'sync')

    async def test_metadata_wrong_subject_nonce_paths_or_lease_rejected(self):
        m=self.metadata('nonce')
        self.assertEqual(self.control._metadata(m,'nonce',self.link),'c'*22)
        for key,value in [('host_id','wrong'),('channel','wrong'),('client_nonce','old'),('lease_seconds',300),
                          ('heartbeat_seconds',4),('safety_poll_required',False),('ws_path','https://example.com/ws')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.control._metadata({**m,key:value},'nonce',self.link)

    async def test_transport_lease_expiry_does_not_grant_safety_freshness(self):
        self.ready()
        self.control.clock=lambda:190
        self.assertFalse(self.control.usable())
        await self.control._post(self.link['api']+'/v1/host/sync',{})
        self.host.credential_revocations.sync.assert_not_awaited()
        self.assertEqual(self.control.lease_until,190)

    async def test_empty_heartbeat_has_no_safety_side_effect(self):
        self.ready()
        self.assertEqual(await self.control._exchange([]),[])
        self.host.credential_revocations.sync.assert_not_awaited()
        self.assertEqual(HEARTBEAT,60)
        self.assertEqual(LEASE,90)
        self.assertEqual(self.posts,[])

    async def test_push_hint_invokes_existing_verified_sync_and_wakes_other_loops(self):
        task=asyncio.create_task(self.control._pushes())
        try:
            self.control.invalidation_streams.update({'credential_revoke','sync','remote_pair'})
            self.control.invalidations.set()
            for _ in range(10):
                await asyncio.sleep(0)
                if self.host.control_sync_wake.is_set(): break
            self.host.credential_revocations.sync.assert_awaited_once()
            self.assertTrue(self.host.control_sync_wake.is_set())
            self.assertTrue(self.host.remote_pair.control_wake.is_set())
            self.assertEqual(self.control.lease_until,0)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task

    async def test_unsupported_hello_cools_down_and_auth_denial_never_downgrades(self):
        for status in (404,401,403,503):
            self.control.http=lambda *a:(status, {})
            await self.control.session(self.link)
            self.assertGreater(self.control.retry_at,100)
            self.assertFalse(self.control.usable())
            if status in (401,403):
                self.assertEqual(await self.control._post(self.link['api']+'/v1/host/sync',{}),(status,{}))
            self.control.denied=None

    async def test_full_jitter_and_retry_after_floor(self):
        ctl=self.control
        self.assertEqual(ctl._retry(),1)
        self.assertEqual(ctl._retry(),2)
        self.assertEqual(ctl._retry(37),37)
        for _ in range(30): delay=ctl._retry()
        self.assertLessEqual(delay,300)

    async def test_serial_exchange_no_overlap(self):
        self.ready()
        current=maximum=0
        original=self.control.ws.send
        async def send(raw):
            nonlocal current,maximum
            current+=1;maximum=max(maximum,current)
            await asyncio.sleep(0)
            await original(raw)
            current-=1
        self.control.ws.send=send
        await asyncio.gather(*(self.control._exchange([]) for _ in range(3)))
        self.assertEqual(maximum,1)

    async def test_same_do_http_exchange(self):
        self.control.connection='c'*22
        self.control.http_mode=True
        self.control.lease_until=190
        def post(url,envelope):
            body=json.loads(wire.unb64u(envelope['body']))
            self.assertEqual(url,self.link['api']+'/v1/host/control/exchange')
            return 200, {'v':1,'connection_id':'c'*22,'nonce':body['nonce'],'results':[], 'committed':[]}
        self.control.http=post
        self.assertEqual(await self.control._exchange([]),[])
        self.host.credential_revocations.sync.assert_not_awaited()

    async def test_host_scheduler_owns_control_and_legacy_safety_independently(self):
        host=serve.Host(self.st,events='jsonl',read_stdin=False)
        self.assertIsInstance(host.control,Control)
        self.assertIsNotNone(host.credential_revocations)
        self.assertIsNotNone(host.remote_pair.control_wake)
        self.assertIsInstance(host.control_sync_wake,asyncio.Event)

    async def test_ws_session_negotiates_routes_real_sync_then_disconnects_to_legacy(self):
        ctl=self.control
        queue=asyncio.Queue()
        calls=[]
        test=self
        class Socket:
            async def recv(self):return json.dumps(test.metadata(self.nonce))
            def __aiter__(self):return self
            async def __anext__(self):
                value=await queue.get()
                if value is None:raise StopAsyncIteration
                return value
            async def send(self, raw):
                request=json.loads(wire.unb64u(json.loads(raw)['body']))
                results=[]
                for item in request['items']:
                    status,body=test.legacy(ctl.base+'/v1/host/'+item['kind'],item['envelope'])
                    results.append({'id':item['id'],'status':status,'headers':{},'body':body})
                await queue.put(json.dumps({'v':1,'connection_id':'c'*22,'nonce':request['nonce'],
                    'results':results,'committed':[r['id'] for r in results]}))
        socket=Socket()
        class Context:
            async def __aenter__(self):return socket
            async def __aexit__(self,*args):pass
        def connect(url,**kwargs):
            calls.append((url,kwargs))
            env=json.loads(kwargs['additional_headers']['X-AgentJ-Control'])
            socket.nonce=json.loads(wire.unb64u(env['body']))['nonce']
            return Context()
        def http(url,env):
            nonce=json.loads(wire.unb64u(env['body']))['nonce']
            return 200,self.metadata(nonce)
        ctl.http=http;ctl.connector=connect
        task=asyncio.create_task(ctl.session(self.link))
        for _ in range(100):
            await asyncio.sleep(.001)
            if ctl.ws:break
        self.assertTrue(ctl.usable())
        result=await asyncio.to_thread(lambda:cloud.send_sync(self.st,[],post=ctl.post))
        self.assertEqual(result.kind,'ok')
        self.assertEqual(calls[0][0],'wss://agentj.app/api/v1/host/control/ws')
        self.assertIsNone(calls[0][1]['ping_interval'])
        await queue.put(None)
        with self.assertRaises(cloud.CloudError):await task
        self.assertFalse(ctl.usable())
        ctl.http=self.legacy
        result=await asyncio.to_thread(lambda:cloud.send_sync(self.st,[],post=ctl.post))
        self.assertEqual(result.kind,'ok')

    async def test_proxy_upgrade_failure_uses_same_do_http(self):
        ctl=self.control
        calls=[]
        test=self
        class Context:
            async def __aenter__(self):raise ConnectionError('proxy refuses websocket')
            async def __aexit__(self,*args):pass
        ctl.connector=lambda *a,**kw:Context()
        def http(url,env):
            body=json.loads(wire.unb64u(env['body']))
            calls.append(url)
            if url.endswith('/hello'):return 200,test.metadata(body['nonce'])
            self.host.stopping.set()
            self.assertTrue(ctl.http_mode)
            return 200,{'v':1,'connection_id':'c'*22,'nonce':body['nonce'],'results':[],'committed':[]}
        ctl.http=http
        await ctl.session(self.link)
        self.assertEqual(calls,[self.link['api']+'/v1/host/control/hello',self.link['api']+'/v1/host/control/exchange'])
        self.assertFalse(ctl.usable())

    async def test_sync_wait_wakes_on_stop_or_push(self):
        for event in (self.host.stopping,self.host.control_sync_wake):
            task=asyncio.create_task(self.control.wait_sync(60))
            await asyncio.sleep(0)
            event.set()
            await asyncio.wait_for(task,1)
            event.clear()

    async def test_oversized_request_never_sent_on_ws_and_uses_legacy(self):
        self.ready()
        send=AsyncMock()
        self.control.ws.send=send
        result=await asyncio.to_thread(lambda:self.control.post(self.link['api']+'/v1/host/sync',
            cloud.envelope(cloud.CTX_SYNC,{'v':1,'t':'sync','padding':'a'*65536},self.host.sk)))
        self.assertEqual(result,(200,{'unbind':[],'agent_name':None}))
        send.assert_not_awaited()

    async def test_ws_auth_error_frame_does_not_downgrade_and_retry_after_survives(self):
        class Socket:
            def __aiter__(self):return self
            async def __anext__(self):return json.dumps({'v':1,'type':'control_error','status':403,'retry_after':120})
        with self.assertRaises(cloud.CloudError):await self.control._reader(Socket())
        self.assertEqual(self.control.denied,403)
        self.assertEqual(self.control.retry_at,220)
        self.control._retry()
        self.assertEqual(self.control.retry_at,220)
        self.assertEqual(await self.control._post(self.link['api']+'/v1/host/sync',{}),(403,{}))
