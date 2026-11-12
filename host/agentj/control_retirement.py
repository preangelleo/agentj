"""0312 authenticated, D1-committed retirement; no transport failure grants legacy authority."""
from __future__ import annotations

import asyncio
import json
import secrets
import time

from . import cloud, credential_revoke, notices
from .reporter import in_daemon_thread

CONTEXT = 'agentj-control-retire-v1'
RECEIPT = 'agentj-control-retired-v1'
MAX_ROUND = 12
FIELDS = {'v', 'host', 'channel', 'retirement_id', 'generation', 'watermark',
          'epoch', 'nonce', 'created_at', 'expires_at'}


def receipt_claim(env, link, channel):
    q = notices.opened(env, RECEIPT)
    if (not isinstance(q, dict) or set(q) != FIELDS or type(q['v']) is not int or q['v'] != 1
            or q['host'] != link['host_id'] or q['channel'] != channel
            or not isinstance(q['retirement_id'], str) or not cloud._UNBIND_ID.fullmatch(q['retirement_id'])
            or not isinstance(q['nonce'], str) or not 16 <= len(q['nonce']) <= 128
            or any(type(q[k]) is not int or q[k] < 0 for k in ('generation', 'watermark', 'epoch', 'created_at', 'expires_at'))
            or q['generation'] < 1 or not q['created_at'] < q['expires_at'] <= q['created_at'] + 300000):
        raise ValueError('control_retirement_receipt')
    return q


def saved_policy(saved, link, channel):
    """A stored receipt grants polling mode, never process-local safety freshness."""
    if saved == {k: link.get(k) for k in ('api', 'host_id', 'linked_at')}:
        return True, 0, False
    if (not isinstance(saved, dict) or set(saved) != {'binding', 'generation', 'receipt'}
            or saved['binding'] != {k: link.get(k) for k in ('api', 'host_id', 'linked_at')}):
        raise ValueError('control_safety_policy')
    generation = saved['generation']
    if type(generation) is not int or generation < 1: raise ValueError('control_safety_policy')
    if saved['receipt'] is None: return True, generation, False
    claim = receipt_claim(saved['receipt'], link, channel)
    if claim['generation'] != generation: raise ValueError('control_safety_policy')
    return False, generation, True


class Retirement:
    def __init__(self, control, link):
        self.control, self.host, self.link = control, control.host, dict(link)
        self.binding = control._binding(link)
        self.base = cloud.api_url(self.host.st, link)
        self.retirement_id = ''
        self.generation = control.safety_generation
        self.watermark = 0
        self.epoch = 0
        self.started = control.clock()
        self.deadline = self.started + 90  # bounded drain; retry starts a fresh authenticated round

    def check(self):
        if (self.control._binding(cloud.read_cloud(self.host.st) or {}) != self.binding
                or self.control.clock() >= self.deadline):
            raise ValueError('control_retirement_binding')

    def request(self, phase, items=None):
        self.check()
        nonce = secrets.token_urlsafe(16)
        body = {'v': 1, 't': 'control_retire', 'channel': self.host.channel,
                'ts': int(time.time()), 'nonce': nonce, 'phase': phase,
                'retirement_id': self.retirement_id, 'generation': self.generation,
                'watermark': self.watermark, 'epoch': self.epoch, 'items': items or []}
        started = self.control.clock()
        status, obj = self.control.http(self.base + '/v1/host/control/retire',
                                        cloud.envelope(CONTEXT, body, self.host.sk), max_response=64*1024)
        self.check()
        if (status != 200 or self.control.clock() - started >= MAX_ROUND
                or not isinstance(obj, dict) or obj.get('v') != 1 or obj.get('nonce') != nonce
                or obj.get('host_id') != self.link['host_id'] or obj.get('channel') != self.host.channel
                or obj.get('phase') != phase
                or not isinstance(obj.get('retirement_id'), str)
                or not cloud._UNBIND_ID.fullmatch(obj['retirement_id'])
                or (self.retirement_id and obj['retirement_id'] != self.retirement_id)
                or any(type(obj.get(k)) is not int or obj[k] < 0 for k in ('generation','watermark','epoch'))
                or obj['generation'] < max(1, self.generation)
                or (self.retirement_id and obj['generation'] != self.generation)
                or not isinstance(obj.get('results'), list)):
            raise ValueError('control_retirement_response')
        if obj['watermark'] < self.watermark or obj['epoch'] < self.epoch:
            raise ValueError('control_retirement_regression')
        if phase == 'commit':
            if any(obj[k] != body[k] for k in ('generation', 'watermark', 'epoch')):
                raise ValueError('control_retirement_commit')
            q = receipt_claim(obj.get('receipt'), self.link, self.host.channel)
            if (q['nonce'] != nonce or any(q[k] != body[k] for k in ('retirement_id','generation','watermark','epoch'))
                    or q['expires_at'] - q['created_at'] > 300000):
                raise ValueError('control_retirement_commit')
        self.retirement_id = obj['retirement_id']
        self.generation, self.watermark, self.epoch = (obj[k] for k in ('generation','watermark','epoch'))
        return obj

    def post(self, url, envelope, **kwargs):
        from .control import KINDS
        self.check()
        path = url[len(self.base):] if url.startswith(self.base + '/') else None
        if path not in KINDS or KINDS[path] not in {'sync','credential_revoke','remote_pair'}:
            raise ValueError('control_retirement_kind')
        item = {'id': secrets.token_urlsafe(16), 'kind': KINDS[path], 'envelope': envelope}
        obj = self.request('drain', [item])
        rows = obj['results']
        if (len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get('id') != item['id']
                or type(rows[0].get('status')) is not int or not 100 <= rows[0]['status'] <= 599
                or not isinstance(rows[0].get('body'), dict) or not isinstance(rows[0].get('headers'), dict)):
            raise ValueError('control_retirement_results')
        result = cloud.Response(rows[0]['body'])
        result.retry_after = rows[0]['headers'].get('retry-after')
        return rows[0]['status'], result

    async def run(self):
        ctl = self.control
        # Existing push/exchange replies cannot reopen a lease during the handover.
        ctl.retiring = True
        ctl.safety_until = 0
        ctl.safety_fence += 1
        try:
            await asyncio.wait_for(in_daemon_thread(self.request, 'begin'), MAX_ROUND + 1)
            ctl.safety_apply_task = asyncio.current_task()
            if await self.host.credential_revocations.sync(post=self.post) is not True:
                raise ValueError('control_retirement_credential')
            if await self.host.credential_revocations.sync(post=self.post) is not True:
                raise ValueError('control_retirement_credential')  # native durable applied epoch ACK
            replies = []
            for _ in range(32):
                self.check()
                result = await in_daemon_thread(lambda: cloud.send_sync(self.host.st, replies, post=self.post))
                if result.kind == 'unbound':
                    await self.host.seat_unbound(self.link)
                    raise ValueError('control_unbound')
                if result.kind != 'ok': raise ValueError('control_retirement_sync')
                self.host.adopt_name(result.name)
                replies = []
                for rid, did in result.unbind:
                    applied = await self.host.remote_unbind(rid, did)
                    if applied is None: raise ValueError('control_retirement_unbind')
                    replies.append((rid, applied))
                if not replies: break
            else: raise ValueError('control_retirement_backlog')
            if await self.host.remote_pair.sync(post=self.post) is False:
                raise ValueError('control_retirement_remote')
            self.check()
            journal = credential_revoke.read(self.host.st)
            if journal['epoch'] != self.epoch or journal.get('ack_epoch') != self.epoch:
                raise ValueError('control_retirement_epoch')
            obj = await asyncio.wait_for(in_daemon_thread(self.request, 'commit'), MAX_ROUND + 1)
            self.check()
            # Verify the final state again after the response. No await between this
            # check, atomic disk receipt and mode transition.
            journal = credential_revoke.read(self.host.st)
            if journal['epoch'] != self.epoch or journal.get('ack_epoch') != self.epoch:
                raise ValueError('control_retirement_epoch')
            self.host.st.write_private(self.host.st.root / 'control_safety.json', json.dumps({
                'binding': self.binding, 'generation': self.generation, 'receipt': obj['receipt']}).encode())
            ctl.handover_pending = True
            ctl.retired = True
            ctl.safety_selected = False
            ctl._disconnect()
        finally:
            ctl.safety_apply_task = None
            ctl.retiring = False
