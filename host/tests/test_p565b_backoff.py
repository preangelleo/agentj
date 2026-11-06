import _hermetic
import asyncio
import io
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agentj import cloud
from agentj.poll_backoff import Backoff, Response, response_delay, retry_after


class BackoffTests(unittest.TestCase):
    def test_sequence_reset_and_bounds(self):
        now = [0]
        b = Backoff(clock=lambda: now[0], jitter=lambda: 0)
        self.assertEqual([b.failed() for _ in range(7)], [4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(b.remaining(), 60)
        now[0] = 61
        self.assertEqual(b.remaining(), 0)
        b.reset()
        self.assertEqual(b.failed(), 4)

    def test_jitter_and_retry_after(self):
        b = Backoff(clock=lambda: 0, jitter=lambda: 1)
        self.assertEqual(b.failed(), 5)
        self.assertEqual(b.failed('http_4xx', 120), 120)
        self.assertEqual(b.failed('http_4xx'), 0)
        self.assertEqual(b.remaining(), 0)

    def test_malicious_retry_after(self):
        for value in (True, False, -1, 'nan', 'inf', 301, '1e999', {}, [], 'x'*500):
            with self.subTest(value=repr(value)[:20]):
                self.assertIsNone(retry_after(value))
        self.assertEqual(retry_after('12'), 12)
        self.assertEqual(retry_after(format_datetime(datetime.fromtimestamp(120, timezone.utc)), now=100), 20)
        self.assertIsNone(retry_after(format_datetime(datetime.fromtimestamp(90, timezone.utc)), now=100))

    def test_transport_metadata_not_json_key(self):
        value = Response({'retry_after': 7})
        value.retry_after = 12
        self.assertEqual(response_delay(429, value), 12)
        self.assertEqual(response_delay(503, value), 12)
        self.assertIsNone(response_delay(403, value))
        self.assertEqual(response_delay(500, {}), 0)

    def test_http_retry_header_forwarded(self):
        response = SimpleNamespace(status=503, headers={'Retry-After': '37'}, read=lambda n: b'{"error":"busy"}')
        class Context:
            def __enter__(self): return response
            def __exit__(self, *args): pass
        opener = SimpleNamespace(open=lambda *a, **k: Context())
        with patch.object(cloud.urllib.request, 'build_opener', return_value=opener):
            status, obj = cloud.post_json('http://localhost/v1', {})
        self.assertEqual(status, 503)
        self.assertEqual(obj.retry_after, 37)
        self.assertEqual(obj, {'error': 'busy'})

    def test_http_error_retry_header_forwarded(self):
        error = cloud.urllib.error.HTTPError('http://localhost/v1',429,'busy',{'Retry-After':'19'},io.BytesIO(b'{}'))
        opener = SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(error))
        with patch.object(cloud.urllib.request, 'build_opener', return_value=opener):
            status, obj = cloud.post_json('http://localhost/v1', {})
        self.assertEqual((status, obj.retry_after), (429, 19))


class AsyncBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_trigger_and_direct_call_cannot_bypass_cooldown(self):
        from agentj.reporter import Reporter
        calls = []
        reporter = Reporter(SimpleNamespace(log=lambda *a, **kw: None), lambda: ([], {}),
                            send=lambda *a: calls.append(1) or cloud.ReportResult('fail','http_4xx',retry_after=10))
        with patch.object(cloud, 'read_cloud', return_value={'host_id':'test'}):
            await reporter.send_once('start')
            reporter.trigger('changed')
            await reporter.send_once('changed')
        self.assertEqual(calls, [1])
        self.assertGreater(reporter.backoff.remaining(), 9)

    async def test_report_success_resets(self):
        from agentj.reporter import Reporter
        reporter=Reporter(SimpleNamespace(log=lambda *a, **kw: None),lambda:([],{}),send=lambda *a:cloud.ReportResult('ok'))
        reporter.backoff.failures=4
        with patch.object(cloud,'read_cloud',return_value={'host_id':'test'}):
            await reporter.send_once('ok')
        self.assertEqual(reporter.backoff.failures,0)

    async def test_report_stops_promptly_during_cooldown(self):
        from agentj.reporter import Reporter
        reporter=Reporter(SimpleNamespace(log=lambda *a, **kw: None),lambda:([],{}),debounce=0)
        reporter.backoff.failed(retry=100)
        task=asyncio.create_task(reporter.run())
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task,.2)

    async def test_bot_registration_singleflight_cache_and_cooldown(self):
        from agentj.bots.runtime import Runtime
        state=SimpleNamespace(lifecycle=lambda bid:{'epoch':1})
        runtime=Runtime(SimpleNamespace(host=SimpleNamespace(),state=lambda:state))
        gate=asyncio.Event();calls=[]
        async def request(bid, backoff):
            calls.append(bid)
            await gate.wait()
            return {'ticket':1},1
        runtime._register_request=request
        first=asyncio.create_task(runtime.register('bot'))
        second=asyncio.create_task(runtime.register('bot'))
        await asyncio.sleep(.01)
        self.assertEqual(calls,['bot'])
        gate.set()
        self.assertEqual(await first,await second)
        await runtime.register('bot')
        self.assertEqual(calls,['bot'])
        runtime.registration_tickets.clear()
        runtime.registration_backoffs['bot'].failed(retry=40)
        with self.assertRaises(Exception):await runtime.register('bot')
        self.assertEqual(calls,['bot'])

    async def test_bot_cancelled_caller_does_not_spawn_second_http(self):
        from agentj.bots.runtime import Runtime
        runtime=Runtime(SimpleNamespace(host=SimpleNamespace(),state=lambda:SimpleNamespace(lifecycle=lambda bid:{'epoch':1})))
        gate=asyncio.Event();calls=[]
        async def request(bid, backoff):
            calls.append(bid)
            await gate.wait()
            return {},1
        runtime._register_request=request
        first=asyncio.create_task(runtime.register('bot'))
        await asyncio.sleep(.01)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        second=asyncio.create_task(runtime.register('bot'))
        await asyncio.sleep(.01)
        self.assertEqual(calls,['bot'])
        gate.set();await second

    async def test_revocation_cooldown_refuses_without_sending_or_extending(self):
        from agentj.credential_revoke import Revocations
        host=SimpleNamespace(st=SimpleNamespace())
        rev=Revocations(host)
        rev.backoff.failed(retry=50)
        rev.poll=lambda *a:self.fail('must not send in cooldown')
        with patch.object(cloud,'read_cloud',return_value={'host_id':'test'}):
            with self.assertRaisesRegex(ValueError,'revocation_sync'):await rev.sync()
        self.assertEqual(rev.results,[])

    async def test_remote_pair_cooldown_preserves_local_expiry_checks(self):
        from agentj.remote_pair import RemotePair
        host=SimpleNamespace(st=SimpleNamespace(log=lambda *a,**kw:None),pairing=None)
        pair=RemotePair(host)
        checks=[];calls=[]
        pair.refresh_waiting=lambda:checks.append(1)
        pair.poll=lambda *a:calls.append(1) or (429,{'retry_after':50})
        async def tick(seconds):
            if len(checks)>=5:raise asyncio.CancelledError
        with patch.object(cloud,'read_cloud',return_value={'host_id':'test'}), patch('agentj.remote_pair.asyncio.sleep',tick):
            with self.assertRaises(asyncio.CancelledError):await pair.run()
        self.assertEqual(calls,[1])
        self.assertEqual(len(checks),5)

    async def test_sync_retains_results_until_successful_ack(self):
        from agentj.serve import Host
        requests=[];count=[0];delays=[]
        class Stop:
            def is_set(self):return count[0]>=4
            async def wait(self):
                count[0]+=1
                raise TimeoutError
        host=SimpleNamespace(stopping=Stop(),st=SimpleNamespace(log=lambda *a,**kw:None),
                             adopt_name=lambda name:None)
        async def unbind(rid,did):return 'revoked'
        host.remote_unbind=unbind
        replies=[cloud.SyncResult('ok',unbind=(('request','device'),)),
                 cloud.SyncResult('fail','http_4xx',retry_after=33),
                 cloud.SyncResult('fail','http_5xx'),cloud.SyncResult('ok')]
        def send(st,results):
            requests.append(list(results))
            return replies[len(requests)-1]
        with patch.object(cloud,'read_cloud',return_value={}),patch.object(cloud,'send_sync',side_effect=send):
            await Host.sync_loop(host)
        self.assertEqual(requests,[[],[('request','revoked')],[('request','revoked')],[('request','revoked')]])

    async def test_bot_epoch_change_does_not_adopt_old_ticket(self):
        from agentj.bots.runtime import Runtime
        epoch=[1]
        runtime=Runtime(SimpleNamespace(host=SimpleNamespace(),state=lambda:SimpleNamespace(lifecycle=lambda bid:{'epoch':epoch[0]})))
        async def request(bid,backoff):
            epoch[0]=2
            return {},1
        runtime._register_request=request
        with self.assertRaises(Exception):await runtime.register('bot')

    async def test_bot_real_registration_throttle_and_fresh_retry(self):
        from agentj.bots.runtime import Runtime
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        config={'slug':'test','title':'Test','description':'','language':'en','enabled':True,'embed_origins':[],'widget':{}}
        state=SimpleNamespace(lifecycle=lambda bid:{'epoch':1,'removed':False},get=lambda bid:config)
        host=SimpleNamespace(st=SimpleNamespace(),channel='test',sk=Ed25519PrivateKey.generate())
        runtime=Runtime(SimpleNamespace(host=host,state=lambda:state))
        payloads=[]
        def post(url,payload):
            payloads.append(payload)
            return (503,{'retry_after':30}) if len(payloads)==1 else (200,{'host_ticket':{'test':1}})
        with patch.object(cloud,'api_url',return_value='http://localhost'),patch.object(cloud,'post_json',side_effect=post):
            with self.assertRaises(Exception):await runtime.register('test')
            with self.assertRaises(Exception):await runtime.register('test')
            self.assertEqual(len(payloads),1)
            cooldown=runtime.registration_backoffs['test']
            self.assertGreater(cooldown.remaining(),29)
            cooldown.clock=lambda:cooldown.until+1
            await runtime.register('test')
        self.assertEqual(len(payloads),2)
        self.assertNotEqual(payloads[0],payloads[1])
        self.assertEqual(cooldown.failures,0)


if __name__=='__main__':unittest.main()
