"""Bounded signed Host writes for #578. Only ciphertext enters HTTPS requests."""
import asyncio
import hashlib
import json
import math
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from . import cloud, media, private_cipher as cipher, wire

DEADLINE = 600


def origin(relay):
    u = urllib.parse.urlsplit(relay)
    scheme = {'wss': 'https', 'ws': 'http'}.get(u.scheme, u.scheme)
    if u.path not in ('', '/') or u.query or u.fragment or u.username or u.password:
        raise ValueError('relay_shape')
    result = urllib.parse.urlunsplit((scheme, u.netloc, '', '', ''))
    cloud.check_url(result)
    return result


def headers(host, method, path, body, now=None, nonce=None):
    stamp = str(int(time.time()) if now is None else now)
    nonce = nonce or wire.b64u(secrets.token_bytes(16))
    digest = hashlib.sha256(body).hexdigest()
    message = f'agentj-file-host-v1\n{method}\n{path}\n{stamp}\n{nonce}\n{digest}'.encode()
    return {'X-AJ-File-Key': wire.b64u(host.st._ed_pub(host.sk)),
            'X-AJ-File-Signature': wire.b64u(host.sk.sign(message)),
            'X-AJ-File-Time': stamp, 'X-AJ-File-Nonce': nonce, 'X-AJ-File-Hash': digest,
            'Content-Type': 'application/octet-stream', 'User-Agent': 'AgentJ-private-file/1',
            'Cache-Control': 'no-store'}


class RelayFiles:
    def __init__(self, host):
        self.host = host
        self.deadline = time.monotonic() + DEADLINE
        self.live = lambda: True
        self.origin = origin(host.cfg['relay'])
        handlers = [cloud._NoRedirect()]
        if urllib.parse.urlsplit(self.origin).hostname in cloud.LOOPBACK:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def request(self, method, path, body):
        # Every 429 resumes this exact ciphertext write with fresh proof, never a new claim.
        failures = 0
        while self.live() and time.monotonic() < self.deadline:
            req = urllib.request.Request(self.origin + path, data=body, method=method,
                                         headers=headers(self.host, method, path, body))
            retry = .15 * (failures + 1)
            try:
                with self.opener.open(req, timeout=min(15,max(.1,self.deadline-time.monotonic()))) as response:
                    if response.status not in (200, 201, 204) or len(response.read(4097)) > 4096:
                        raise ValueError('relay_response')
                    return
            except urllib.error.HTTPError as error:
                status = error.code
                value = error.headers.get('Retry-After','1') if error.headers else '1'
                error.close()
                if status == 429:
                    retry = min(60,max(1,int(value))) if str(value).isdecimal() else 1
                elif status in (500, 502, 503, 504):
                    failures += 1
                else:
                    raise ValueError('relay_refused') from None
            except (urllib.error.URLError, OSError, TimeoutError):
                failures += 1
            if failures >= 3:
                break
            until = min(self.deadline,time.monotonic()+retry)
            while self.live() and time.monotonic() < until:
                time.sleep(min(.25,until-time.monotonic()))
        raise ValueError('relay_unavailable')

    async def upload(self, direct, fid, row, claim):
        from .private_direct import source
        path = f'/v1/file/{self.host.channel}/{fid}'
        rec = row['rec']
        count = math.ceil(rec['bytes'] / cipher.CHUNK)
        meta = {'v': 1, 'total': rec['bytes'], 'chunk_size': cipher.CHUNK, 'chunks': count, 'expires': row['expires']}
        deadline = self.deadline = time.monotonic() + DEADLINE
        self.live = lambda: direct.valid(fid,row,claim)
        async def put(method, suffix, body):
            if not direct.valid(fid, row, claim) or time.monotonic() >= deadline:
                raise media.Gone('changed')
            await asyncio.to_thread(self.request, method, path + suffix, body)
            if not direct.valid(fid, row, claim):
                raise media.Gone('changed')
        encode = lambda value: json.dumps(value, separators=(',', ':')).encode()
        await put('PUT', '', encode(meta))
        try:
            digest = hashlib.sha256()
            for index in range(count):
                data = await asyncio.to_thread(source, rec, index)
                digest.update(data)
                encrypted = cipher.encrypt(claim['key'], claim['nonce_prefix'], self.host.channel, fid,
                                           claim['transfer'], index, rec['bytes'], data)
                await put('PUT', f'/chunk/{index}', encrypted)
                claim['uploaded'] = min(rec['bytes'], (index + 1) * cipher.CHUNK)
            if digest.hexdigest() != rec['sha256']:
                raise media.Gone('changed')
            await asyncio.to_thread(source, rec)
            await put('POST', '/ready', b'')
            await put('PUT', '/claim', encode({'transfer': claim['transfer'], 'device_pk': wire.b64u(claim['signer'])}))
            return self.origin + path
        except BaseException:
            # Best-effort ciphertext cleanup only; local claim stays spent even if HTTPS is down.
            try:
                await asyncio.to_thread(self.request, 'DELETE', path, b'')
            except Exception:
                pass
            raise
