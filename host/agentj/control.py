"""0312 transport adapter. D1 handlers remain authoritative; an empty pong grants no safety freshness.

Explicit safety protocol negotiation replaces legacy safety polling with a bounded
lease. Only a current, drained outbox exchange renews that lease.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import random
import secrets
import time

from . import cloud
from .poll_backoff import response_delay, retry_after
from .reporter import in_daemon_thread

HELLO = 'agentj-control-hello-v1'
EXCHANGE = 'agentj-control-exchange-v1'
MAX_FRAME = 65536
HEARTBEAT = 60
LEASE = 90
KINDS = {'/v1/host/sync': 'sync', '/v1/host/report': 'report',
         '/v1/host/credential-revoke': 'credential_revoke', '/v1/host/remote-pair': 'remote_pair'}
SAFETY_KINDS = {'credential_revoke', 'remote_pair'}


class Control:
    def __init__(self, host, *, post=cloud.post_json, connector=None, clock=time.monotonic, jitter=random.random):
        self.host, self.http, self.connector = host, post, connector
        self.clock, self.jitter = clock, jitter
        self.loop = None
        self.ws = None
        self.connection = None
        self.base = None
        self.lease_until = 0.0
        self.denied = None
        self.failures = 0
        self.retry_at = 0.0
        self.serial = asyncio.Lock()
        self.pending = {}
        self.invalidations = asyncio.Event()
        self.invalidation_streams = set()
        self.http_mode = False
        self.safety_selected = False
        self.safety_until = 0.0
        self.safety_deadline = 0.0
        self.safety_apply_task = None
        self.safety_generation = 0
        self.safety_applied_generation = 0
        self.safety_pending = set()
        self.safety_fence = 0
        self.retiring = False
        self.retired = False
        self.handover_pending = False
        self.safety_recovery = asyncio.Lock()
        marker = self.host.st.root / 'control_safety.json'
        if marker.exists():
            try:
                saved = json.loads(marker.read_text())
                link = cloud.read_cloud(self.host.st)
                if link:
                    # A different actual binding does not inherit this policy.
                    binding = saved.get('binding', saved) if isinstance(saved, dict) else None
                    if binding != self._binding(link) and isinstance(binding, dict) and set(binding) == {'api','host_id','linked_at'}:
                        self.safety_selected = False
                    else:
                        from .control_retirement import saved_policy
                        self.safety_selected, self.safety_generation, self.retired = saved_policy(saved, link, self.host.channel)
                        self.handover_pending = self.retired
            except Exception:
                self.safety_selected = True  # malformed safety policy cannot reopen legacy


    @staticmethod
    def _binding(link):
        return {k: link.get(k) for k in ('api', 'host_id', 'linked_at')}

    def _hello(self):
        body = {'v': 1, 't': 'control_hello', 'channel': self.host.channel,
                'ts': int(time.time()), 'nonce': secrets.token_urlsafe(16)}
        return body, cloud.envelope(HELLO, body, self.host.sk)

    def _metadata(self, value, nonce, link):
        if self.retiring or self.retired: raise ValueError('control_retired')
        if (not isinstance(value, dict) or value.get('v') != 1
                or value.get('transport') != 'websocket'
                or value.get('host_id') != link['host_id'] or value.get('channel') != self.host.channel
                or value.get('client_nonce') != nonce
                or not isinstance(value.get('connection_id'), str)
                or not 16 <= len(value['connection_id']) <= 128
                or type(value.get('heartbeat_seconds')) is not int or value['heartbeat_seconds'] != HEARTBEAT
                or type(value.get('lease_seconds')) is not int or value['lease_seconds'] != LEASE
                or not (value.get('safety_poll_required') is True or
                        (value.get('safety_poll_required') is False and value.get('safety_protocol') == 1))
                or value.get('ws_path') != '/v1/host/control/ws'
                or value.get('exchange_path') != '/v1/host/control/exchange'):
            raise ValueError('control_metadata')
        if value.get('safety_poll_required') is False:
            self.host.st.write_private(self.host.st.root / 'control_safety.json',
                json.dumps(self._binding(link) if self.safety_generation == 0 else {
                    'binding': self._binding(link), 'generation': self.safety_generation, 'receipt': None}).encode())
            self.safety_selected = True
            self.safety_until = 0
        elif self.safety_selected:
            raise ValueError('control_safety_downgrade')
        return value['connection_id']

    def _retry(self, minimum=0):
        self.failures = min(12, self.failures + 1)
        # Full jitter and a nonzero floor avoid synchronized reconnects and busy loops.
        delay = max(1.0, min(300, 2 ** self.failures) * self.jitter(), minimum, self.retry_at - self.clock())
        self.retry_at = self.clock() + delay
        return delay

    def _disconnect(self):
        self.ws = None
        self.connection = None
        self.http_mode = False
        self.lease_until = 0
        for fut in tuple(self.pending.values()):
            if not fut.done(): fut.set_exception(cloud.CloudError('network'))
        self.pending.clear()

    def usable(self):
        return self.connection is not None and self.clock() < self.lease_until and (self.ws is not None or self.http_mode)

    def safety_ready(self):
        return not (self.retiring or self.handover_pending) and (not self.safety_selected or self.clock() < self.safety_until)

    def require_safety(self):
        if not self.safety_ready(): raise ValueError('control_safety_expired')

    def _safety(self, value, started):
        if self.retiring or self.retired or not self.safety_selected: return
        self.safety_until = 0
        if (not isinstance(value, dict) or set(value) != {'generation','lease_seconds','lease_granted','pending'}
                or type(value['generation']) is not int or value['generation'] < max(1, self.safety_generation)
                or type(value['lease_seconds']) is not int or not 0 <= value['lease_seconds'] <= LEASE
                or type(value['lease_granted']) is not bool
                or (value['lease_granted'] and value['lease_seconds'] == 0)
                or not isinstance(value['pending'], list) or len(value['pending']) > 32):
            raise ValueError('control_safety')
        pending = set()
        for row in value['pending']:
            if (not isinstance(row, dict) or set(row) != {'generation','seq'}
                    or type(row['generation']) is not int or row['generation'] != value['generation']
                    or type(row['seq']) is not int or row['seq'] < 1):
                raise ValueError('control_safety_pending')
            pending.add((row['generation'], row['seq']))
        if len(pending) != len(value['pending']) or (pending and value['lease_granted']):
            raise ValueError('control_safety_pending')
        if self.safety_generation != value['generation']:
            link = cloud.read_cloud(self.host.st)
            if not link: raise ValueError('control_safety_binding')
            self.host.st.write_private(self.host.st.root / 'control_safety.json', json.dumps({
                'binding': self._binding(link), 'generation': value['generation'], 'receipt': None}).encode())
        self.safety_generation = value['generation']
        self.safety_pending = pending
        fresh = self.safety_applied_generation == value['generation']
        self.safety_until = started + value['lease_seconds'] if value['lease_granted'] and fresh else 0
        if self.safety_until: self.safety_deadline = self.safety_until
        if pending or not fresh: self.invalidations.set()

    async def _exchange(self, items, safety_ack=None):
        async with self.serial:
            if self.retiring or self.retired or not self.usable(): raise cloud.CloudError('network')
            nonce = secrets.token_urlsafe(16)
            body = {'v': 1, 't': 'control_exchange', 'channel': self.host.channel,
                    'ts': int(time.time()), 'nonce': nonce, 'connection_id': self.connection, 'items': items}
            if safety_ack is not None: body['safety_ack'] = safety_ack
            envelope = cloud.envelope(EXCHANGE, body, self.host.sk)
            started = self.clock()
            fence = self.safety_fence
            if self.ws is None:
                status, obj = await asyncio.wait_for(in_daemon_thread(
                    self.http, self.base + '/v1/host/control/exchange', envelope), cloud.HTTP_TIMEOUT + 1)
                if status != 200:
                    if status in (401, 403): self.denied = status
                    raise cloud.CloudError('network')
            else:
                fut = asyncio.get_running_loop().create_future()
                self.pending[nonce] = fut
                try:
                    encoded = json.dumps(envelope, separators=(',', ':'))
                    if len(encoded.encode()) > MAX_FRAME: raise cloud.CloudError('too_large')
                    await self.ws.send(encoded)
                    obj = await asyncio.wait_for(fut, cloud.HTTP_TIMEOUT)
                finally:
                    self.pending.pop(nonce, None)
            if (not isinstance(obj, dict) or obj.get('v') != 1 or obj.get('connection_id') != self.connection
                    or obj.get('nonce') != nonce or not isinstance(obj.get('results'), list)
                    or not isinstance(obj.get('committed'), list)):
                raise ValueError('control_response')
            ids = [item['id'] for item in items]
            rows = obj['results']
            if (len(rows) != len(items) or [r.get('id') for r in rows] != ids
                    or any(type(r.get('status')) is not int or not 100 <= r['status'] <= 599
                           or not isinstance(r.get('body'), dict) or not isinstance(r.get('headers'), dict) for r in rows)
                    or len(set(obj['committed'])) != len(obj['committed'])
                    or not set(obj['committed']).issubset(ids)):
                raise ValueError('control_results')
            # Starting time, not arrival time: slow/hung exchanges cannot extend the lease.
            self.lease_until = started + LEASE
            prior_deadline = self.safety_deadline
            self._safety(obj.get('safety'), started)
            if fence != self.safety_fence:
                self.safety_until = 0
                self.safety_deadline = prior_deadline
            return rows

    async def _post(self, url, payload):
        base = self.base
        path = url[len(base):] if base and url.startswith(base + '/') else None
        if path not in KINDS: return None
        if self.denied: return self.denied, {}
        if not self.usable():
            return (503, {}) if self.safety_selected and KINDS[path] in SAFETY_KINDS else None
        item = {'id': secrets.token_urlsafe(16), 'kind': KINDS[path], 'envelope': payload}
        try:
            row, = await self._exchange([item])
            obj = cloud.Response(row['body'])
            obj.retry_after = row['headers'].get('retry-after')
            return row['status'], obj
        except Exception:
            # Stage 1 only: authority never moved from D1. Existing handlers own replay recovery.
            self._disconnect()
            self._retry()
            if self.denied: return self.denied, {}
            if self.safety_selected and KINDS[path] in SAFETY_KINDS: return 503, {}
            return None

    def post(self, url, payload, **kwargs):
        """Called only by existing blocking report/sync handlers on their daemon thread."""
        if self.retired and any(url.endswith('/v1/host/' + p) for p in ('credential-revoke','remote-pair','sync','report')):
            # A routing hint only: Worker reauthenticates the original envelope
            # and checks its durable binding-specific retirement tombstone.
            kwargs['control_retired'] = True
            if url.endswith('/v1/host/report'):
                kwargs.setdefault('max_response', 128 * 1024)
            return self.http(url, payload, **kwargs)
        if self.loop is not None and self.loop.is_running():
            task = asyncio.run_coroutine_threadsafe(self._post(url, payload), self.loop)
            try:
                result = task.result(cloud.HTTP_TIMEOUT + 2)
                if result is not None: return result
            except (TimeoutError, concurrent.futures.TimeoutError):
                task.cancel()
                raise cloud.CloudError('timeout')
        if self.safety_selected and any(url.endswith('/v1/host/' + p) for p in ('credential-revoke','remote-pair')):
            return 503, {}
        return (cloud.report_post_json(url, payload) if self.http is cloud.post_json and url.endswith("/v1/host/report")
                else self.http(url, payload, **kwargs))

    async def _reader(self, ws):
        async for raw in ws:
            if not isinstance(raw, str) or len(raw.encode()) > MAX_FRAME: raise ValueError('control_frame')
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get('v') == 1 and obj.get('type') == 'control_error':
                status = obj.get('status')
                if type(status) is not int or not 400 <= status <= 599: raise ValueError('control_error')
                if status in (401, 403):
                    self.denied = status
                    self.safety_until = 0
                self._retry(retry_after(obj.get('retry_after')) or 0)
                raise cloud.CloudError('network')
            if isinstance(obj, dict) and obj.get('v') == 1 and obj.get('type') == 'invalidate':
                streams = obj.get('streams')
                if not isinstance(streams, list) or not streams or not set(streams).issubset({'credential_revoke','remote_pair','sync'}):
                    raise ValueError('control_invalidation')
                if self.safety_selected:
                    self.safety_until = 0
                    self.safety_fence += 1
                self.invalidation_streams.update(streams)
                self.invalidations.set()
                continue
            nonce = obj.get('nonce') if isinstance(obj, dict) else None
            fut = self.pending.get(nonce) if isinstance(nonce, str) else None
            if fut is None or fut.done(): raise ValueError('control_unsolicited')
            fut.set_result(obj)

    async def wait_sync(self, timeout):
        stop = asyncio.create_task(self.host.stopping.wait())
        wake = asyncio.create_task(self.host.control_sync_wake.wait())
        try:
            await asyncio.wait({stop, wake}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel(); wake.cancel()
            await asyncio.gather(stop, wake, return_exceptions=True)
            self.host.control_sync_wake.clear()

    async def _pushes(self):
        while True:
            await self.invalidations.wait()
            streams, self.invalidation_streams = self.invalidation_streams, set()
            self.invalidations.clear()
            if self.safety_selected:
                try:
                    async with self.safety_recovery:
                        if self.retiring or self.retired: continue
                        # Fetch pending from the authenticated, nonce-bound response; an
                        # unsolicited hint can only fence, never authorize or ACK a row.
                        await self._exchange([])
                        pending = set(self.safety_pending)
                        generation = self.safety_generation
                        if pending or self.safety_applied_generation != generation:
                            self.safety_apply_task = asyncio.current_task()
                            await self.host.credential_revocations.sync()
                            await self.host.credential_revocations.sync()  # applied epoch ACK
                            link = cloud.read_cloud(self.host.st)
                            replies = []
                            for _ in range(32):
                                result = await in_daemon_thread(lambda: cloud.send_sync(self.host.st, replies, post=self.post))
                                if result.kind == 'unbound':
                                    await self.host.seat_unbound(link)
                                    raise ValueError('control_unbound')
                                if result.kind != 'ok': raise ValueError('control_sync')
                                self.host.adopt_name(result.name)
                                replies = []
                                for rid, did in result.unbind:
                                    applied = await self.host.remote_unbind(rid, did)
                                    if applied is None: raise ValueError('control_unbind_pending')
                                    replies.append((rid, applied))
                                if not replies: break
                            else: raise ValueError('control_sync_pending')
                            await self.host.remote_pair.sync()
                            self.safety_applied_generation = generation
                            await self._exchange([], [{'generation': g, 'seq': n} for g, n in sorted(pending)])
                except Exception:
                    self.safety_until = 0
                    self.host.st.log('control_push', status='sync_failed')
                finally:
                    self.safety_apply_task = None
                continue
            # Legacy protocol hints still use the original verified snapshot.
            if 'credential_revoke' in streams:
                try: await self.host.credential_revocations.sync()
                except Exception: self.host.st.log('control_push', status='sync_failed')
            if 'sync' in streams:
                wake = getattr(self.host, 'control_sync_wake', None)
                if wake is not None: wake.set()
            if 'remote_pair' in streams:
                wake = getattr(getattr(self.host, 'remote_pair', None), 'control_wake', None)
                if wake is not None: wake.set()

    async def session(self, link):
        self.base = cloud.api_url(self.host.st, link)
        body, envelope = self._hello()
        code, obj = await asyncio.wait_for(in_daemon_thread(self.http,
            self.base + '/v1/host/control/hello', envelope), cloud.HTTP_TIMEOUT + 1)
        if code in (401, 403):
            self.denied = code
            self._retry(300)
            return
        if code != 200:
            self._retry(300 if code == 404 else response_delay(code, obj) or 0)
            return
        connection = self._metadata(obj, body['nonce'], link)
        self.denied = None
        connector = self.connector
        if connector is None:
            from websockets.asyncio.client import connect
            connector = connect
        body, envelope = self._hello()
        url = self.base.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + '/v1/host/control/ws'
        established = False
        try:
            async with connector(url, additional_headers={'X-AgentJ-Control': json.dumps(envelope, separators=(',', ':'))},
                                 ping_interval=None, open_timeout=cloud.HTTP_TIMEOUT, max_size=MAX_FRAME) as ws:
                raw = await asyncio.wait_for(ws.recv(), cloud.HTTP_TIMEOUT)
                self.connection = self._metadata(json.loads(raw), body['nonce'], link)
                established = True
                self.ws, self.http_mode = ws, False
                self.lease_until = self.clock() + LEASE
                reader = asyncio.create_task(self._reader(ws))
                try:
                    if self.safety_selected: await self._exchange([])
                    while not self.host.stopping.is_set():
                        done, _ = await asyncio.wait({reader}, timeout=HEARTBEAT)
                        if done:
                            reader.result()
                            raise cloud.CloudError('network')
                        await self._exchange([])
                        self.failures = 0
                finally:
                    reader.cancel()
                    with contextlib.suppress(asyncio.CancelledError): await reader
        except Exception as error:
            response = getattr(error, 'response', None)
            code = getattr(response, 'status_code', None)
            if code in (401, 403):
                self.denied = code
                raise
            if established:
                raise
            # Corporate proxies may reject upgrades. Reuse the hello's same-host DO
            # connection; never treat its empty exchange as a revocation snapshot.
            self.connection, self.http_mode = connection, True
            self.lease_until = self.clock() + LEASE
            while not self.host.stopping.is_set():
                await self._exchange([])
                self.failures = 0
                try: await asyncio.wait_for(self.host.stopping.wait(), HEARTBEAT)
                except TimeoutError: pass
        finally:
            self._disconnect()

    async def _watchdog(self):
        while not self.host.stopping.is_set():
            if (self.safety_selected or self.handover_pending) and self.clock() >= self.safety_deadline:
                ws = getattr(self.host, 'ws', None)
                if ws is not None:
                    self.host.relay_up = False
                    await ws.close()
            await asyncio.sleep(.25)

    async def _legacy_handover(self):
        async with self.safety_recovery:
            if not self.handover_pending: return
            link = cloud.read_cloud(self.host.st)
            if not link: raise ValueError('control_handover_binding')
            binding = self._binding(link)
            saved = json.loads((self.host.st.root / 'control_safety.json').read_text())
            if saved.get('binding') != binding: raise ValueError('control_handover_binding')
            self.safety_apply_task = asyncio.current_task()
            try:
                # Receipt permits legacy mode; these original direct signed polls
                # establish process freshness before customer traffic reopens.
                if await self.host.credential_revocations.sync(post=self.post) is not True:
                    raise ValueError('control_handover_credential')
                if await self.host.remote_pair.sync(post=self.post) is False:
                    raise ValueError('control_handover_remote')
                if self._binding(cloud.read_cloud(self.host.st) or {}) != binding:
                    raise ValueError('control_handover_binding')
                self.handover_pending = False
                wake = getattr(self.host.remote_pair, 'control_wake', None)
                if wake is not None: wake.set()
            finally:
                self.safety_apply_task = None

    async def _retire(self, link):
        from .control_retirement import Retirement
        async with self.safety_recovery:
            if self.safety_selected and not self.retired:
                await Retirement(self, link).run()
        if self.handover_pending: await self._legacy_handover()

    async def run(self):
        self.loop = asyncio.get_running_loop()
        pushes = asyncio.create_task(self._pushes())
        watchdog = asyncio.create_task(self._watchdog())
        try:
            while not self.host.stopping.is_set():
                delay = max(0, self.retry_at - self.clock())
                if delay:
                    try: await asyncio.wait_for(self.host.stopping.wait(), delay)
                    except TimeoutError: pass
                    continue
                link = cloud.read_cloud(self.host.st)
                if link:
                    try:
                        if self.retired:
                            if self.handover_pending: await self._legacy_handover()
                            self._retry(4 if self.handover_pending else 60)
                            continue
                        await self.session(link)
                    except Exception:
                        self._retry()
                        if self.retired and self.handover_pending:
                            self.retry_at = self.clock() + 4
                        self.host.st.log('control_transport', status='recovery' if self.safety_selected else 'legacy')
                    if self.safety_selected:
                        try: await self._retire(link)
                        except Exception:
                            self.host.st.log('control_retirement', status='retry')
                        # A missing receipt keeps the fence and an active recovery
                        # attempt every four seconds, even after a 404/DO outage.
                        self.retry_at = min(self.retry_at, self.clock() + 4)
                else:
                    self._retry(60)
        finally:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError): await watchdog
            pushes.cancel()
            with contextlib.suppress(asyncio.CancelledError): await pushes
            self._disconnect()
