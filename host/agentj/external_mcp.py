"""#474: authenticated one-way MCP admission; results never leave the host.

Token hashes, origins and durable admission receipts are host-only. A committed
admission is never replayed after a crash, including uncertain submit outcomes.
"""
from __future__ import annotations
import asyncio
import collections
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlsplit
from . import hooks, provenance
from .compose import Send

REVISION = '2025-06-18'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
KEY = re.compile(r'[\x21-\x7e]{1,128}')


def origins(raw):
    if not isinstance(raw, list) or len(raw) > 16:
        raise ValueError('origins')
    out = []
    for item in raw:
        u = urlsplit(item) if isinstance(item, str) else None
        if not u or u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError('origin')
        if item not in out:
            out.append(item)
    return out


def digest(hid, value):
    return hashlib.sha256(('agentj/external-mcp/token/v1\n' + hid + '\n' + value).encode()).hexdigest()


def reply(status, code=200, **kw):
    return code, {'mcp': 1, 'status': status, **kw}


class Service:
    def __init__(self, service):
        self.service, self.store, self.host = service, service.store, service.host
        self.path = self.store.dir / 'external-mcp.json'
        self.doc = self.store._read(self.path, {})
        for key in ('connectors', 'receipts'):
            if not isinstance(self.doc.get(key), dict):
                self.doc[key] = {}
        self.lock = asyncio.Lock()

    def save(self):
        self.store._write(self.path, self.doc)

    def get(self, hid):
        return self.doc['connectors'].get(hid)

    def ids(self):
        return [hid for hid, c in self.doc['connectors'].items() if c.get('state') == 'active'
                and (h := self.store.get(hid)) and h.get('state') == 'active']

    def views(self):
        return [{k: c.get(k) for k in ('id', 'name', 'origins', 'state', 'created', 'last')} |
                {'scope': 'request-only', 'url': self.service.url({'id': c['id']})}
                for c in self.doc['connectors'].values()]

    async def manage(self, op, obj):
        """Called only by paired phone dispatch. The signed card is still required."""
        async with self.lock:
            if op not in ('mcp-create', 'mcp-rotate', 'mcp-revoke'):
                return {'ok': False, 'why': 'shape'}
            c = self.get(obj.get('id')) if isinstance(obj.get('id'), str) else None
            if op == 'mcp-create':
                try:
                    allowed = origins(obj.get('origins', []))
                    name = obj.get('name')
                    if not isinstance(name, str) or not 1 <= len(name) <= 40 or any(ord(x) < 32 for x in name):
                        raise ValueError('name')
                except (TypeError, ValueError):
                    return {'ok': False, 'why': 'shape'}
                if len(self.doc['connectors']) >= 20 or len(self.store.live()) >= hooks.HOOKS_MAX:
                    return {'ok': False, 'why': 'quota'}
                h = hooks.build({'preset': 'bearer', 'life': 'resident', 'name': name}, 'phone', self.service.clock())
                c = {'id': h['id'], 'name': name, 'origins': allowed, 'state': 'pending', 'created': round(self.service.clock())}
                self.doc['connectors'][h['id']] = c
                self.store.doc['hooks'][h['id']] = h
                self.store.save(); self.save()
            elif c is None:
                return {'ok': False, 'why': 'not_found'}
            text = ('External assistant / 单向外部助理: ' + c['name'] + '\n' + op + '\n' +
                    'Scope / 范围: request-only; no replies/results leave Agent J.\n' +
                    '请求正文经过托管中继内存；回复和结果只留在主机与手机。\nOrigins: ' + ', '.join(c['origins']))
            try:
                answer = await self.host.ask('AgentJHook', {'card': text, 'hook': c['id'], 'mcp': op}, batch=False, grant_info=True)
            except Exception:
                answer = {}
            from . import application_ingress
            if answer.get('behavior') != 'allow' or not application_ingress.allowed(self.host):
                if op == 'mcp-create':
                    c['state'] = 'revoked'; self.store.end(self.store.get(c['id']), 'revoked'); self.save()
                self.service.changed()
                return {'ok': False, 'why': 'denied'}
            if op == 'mcp-revoke':
                c['state'] = 'revoked'; c.pop('hash', None)
                self.store.end(self.store.get(c['id']), 'revoked'); self.save()
            else:
                if op == 'mcp-rotate' and c.get('state') != 'active':
                    return {'ok': False, 'why': 'gone'}
                token = hooks.b64u(secrets.token_bytes(32))
                # Save replacement first: old token ceases working immediately.
                c['hash'], c['state'] = digest(c['id'], token), 'active'
                h = self.store.get(c['id']); h['state'] = 'active'
                h['approved'] = {'actor': application_ingress.actor(self.host), 'at': round(self.service.clock())}
                self.store.save(); self.save()
                box = getattr(getattr(self.host, 'elevate', None), 'out', None)
                try:
                    pickup = await box.send({'name': 'External assistant token', 'purpose': c['name'], 'kind': 'text', 'value': token}) if box else {}
                except (Exception, asyncio.CancelledError):
                    c['state'] = 'revoked'; c.pop('hash', None)
                    self.store.end(h, 'revoked'); self.save()
                    self.service.changed(); self.service.ensure_net()
                    raise
                token = None
                if pickup.get('result') != 'sent':
                    c['state'] = 'revoked'; c.pop('hash', None)
                    self.store.end(h, 'revoked'); self.save()
                    self.service.changed(); self.service.ensure_net()
                    return {'ok': False, 'why': 'pickup_failed'}
            self.service.log('mcp_connector', op=op, connector=c['id'][:8])
            self.service.changed(); self.service.ensure_net()
            return {'ok': True}

    async def handle(self, meta, body):
        c = self.get(meta.get('h'))
        if not c:
            return reply('invalid', 404)
        if c.get('state') != 'active':
            return reply('refused', 410)
        h = self.store.get(c['id'])
        if not h or h.get('state') != 'active' or not h.get('enabled', True) or self.host.stopped():
            return reply('refused', 423)
        headers = meta.get('hd') or {}
        # Relay header representation is a lowercase map, never user-source authority.
        if not isinstance(headers, dict):
            return reply('invalid', 400)
        heads = {str(k).lower(): v for k, v in headers.items()}
        auth = heads.get('authorization', '')
        token = auth[7:] if isinstance(auth, str) and auth.startswith('Bearer ') else ''
        if not TOKEN.fullmatch(token) or not hmac.compare_digest(c.get('hash', ''), digest(c['id'], token)):
            return reply('refused', 401)
        origin = heads.get('origin')
        if origin is not None and origin not in c['origins']:
            return reply('refused', 403)
        if meta.get('m') != 'POST':
            return reply('method', 405)
        if len(body) > hooks.BODY_MAX:
            return reply('invalid', 413)
        try:
            req = json.loads(body)
            if not isinstance(req, dict) or req.get('jsonrpc') != '2.0' or set(req) - {'jsonrpc', 'id', 'method', 'params'}:
                raise ValueError()
            rid = req.get('id')
            if 'id' in req and not (type(rid) is int or isinstance(rid, str) and re.fullmatch(r'[\x21-\x7e]{1,128}', rid)):
                raise ValueError()
            method = req.get('method')
            params = req.get('params', {})
            if not isinstance(params, dict):
                raise ValueError()
            if method == 'initialize':
                if params.get('protocolVersion') != REVISION:
                    raise ValueError()
                return reply('authenticated')
            if heads.get('mcp-protocol-version') != REVISION:
                raise ValueError()
            if method in ('ping', 'tools/list', 'notifications/initialized'):
                return reply('authenticated')
            if method != 'tools/call' or 'id' not in req or params.get('name') != 'agentj_request' or set(params) != {'name', 'arguments'}:
                raise ValueError()
            args = params['arguments']
            if not isinstance(args, dict) or set(args) != {'text', 'idempotency_key'}:
                raise ValueError()
            text, key = args['text'], args['idempotency_key']
            if not isinstance(text, str) or not 1 <= len(text) <= 16000 or not isinstance(key, str) or not KEY.fullmatch(key):
                raise ValueError()
        except (ValueError, TypeError, UnicodeDecodeError):
            return reply('invalid', 400)
        async with self.lock:
            if c.get('state') != 'active':
                return reply('refused', 410)
            h = self.store.get(c['id'])
            if not h or h.get('state') != 'active' or not h.get('enabled', True) or self.host.stopped():
                return reply('refused', 423)
            if not hmac.compare_digest(c.get('hash', ''), digest(c['id'], token)):
                return reply('refused', 401)
            h = self.store.get(c['id'])
            if not h or h.get('state') != 'active' or not h.get('enabled', True) or self.host.stopped():
                return reply('refused', 423)
            receipts = self.doc['receipts']
            rkey = hashlib.sha256((c['id'] + '\n' + key).encode()).hexdigest()
            content = hashlib.sha256(text.encode()).hexdigest()
            if rkey in receipts:
                rec = receipts[rkey]
                if rec['hash'] != content:
                    return reply('conflict', 409)
                return reply('duplicate', delivery=rec['delivery']) if rec.get('state') == 'queued' else reply('refused', 503)
            # Bound persistent records, never evict an old key then risk replaying it.
            q = self.service.minute.setdefault(c['id'], collections.deque())
            now = self.service.clock()
            while q and now - q[0] >= 60:
                q.popleft()
            if len(receipts) >= 5000 or len(q) >= hooks.PER_MIN or not self.store.take_day(c['id']):
                return reply('refused', 429)
            agent = getattr(self.host, 'agent', None)
            if not agent or not callable(getattr(agent, 'submit', None)):
                return reply('refused', 503)
            did = 'd' + secrets.token_hex(6)
            q.append(now)
            receipts[rkey] = {'hash': content, 'delivery': did, 'at': round(now), 'state': 'pending'}
            self.save()  # durable before submit: crash/exception never resubmits
            try:
                from . import privacy
                page = self.host.hist_add({'k': 'agent', 'via': 'mcp', 'name': c.get('name', 'API'),
                                           'text': privacy.owner_text(text)}, '', 'open')
                send = Send('mcp', secrets.token_urlsafe(16), page['id'], text=text, by='external-mcp')
                send.source = provenance.external_mcp(c['id'])
                agent.submit(send)
            except Exception:
                return reply('refused', 503)
            receipts[rkey]['state'] = 'queued'
            c['last'] = {'at': round(self.service.clock()), 'delivery': did}; self.save()
            return reply('accepted', delivery=did)
