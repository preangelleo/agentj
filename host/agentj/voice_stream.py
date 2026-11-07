"""#441 opt-in PCM streaming prototype. Content stays on the paired Noise channel.
Qwen WS dialect: start {sample_rate,format}, binary s16le, finish; incoming
{type: partial|final, text}. No HTTP transcription fallback or cloud ASR.
"""
import asyncio
import base64
import contextlib
import ipaddress
import inspect
import json
import math
import os
import re
import struct
import time
from urllib.parse import urlsplit, urlunsplit

CAP = 'voice_stream_v1'
RATE, CHUNK, SECONDS, QUEUE = 16000, 6400, 30, 8
LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


def endpoint(cfg):
    from . import preferences as p, voice
    if p.get(cfg, 'voice.asr.mode') != 'custom':
        raise ValueError('stream_unavailable')
    # Reuse the existing owner-selected URL validator; never accept a phone URL.
    u = urlsplit(voice.asr_endpoint(p.get(cfg, 'voice.asr.base_url')))
    try:
        addr = ipaddress.ip_address(u.hostname)
        if not (addr.is_loopback or any(addr in net for net in LOCAL_NETS if addr.version == net.version)):
            raise ValueError()
    except ValueError:
        raise ValueError('local_stream_required') from None
    return urlunsplit(('wss' if u.scheme == 'https' else 'ws', u.netloc,
                       u.path.removesuffix('/transcriptions') + '/stream', '', ''))


async def connect(cfg):
    from . import preferences as p
    from websockets.asyncio.client import connect as ws_connect
    url = endpoint(cfg)
    name = p.get(cfg, 'voice.asr.key_env')
    key = os.environ.get(name, '') if name else ''
    if name and not key:
        raise ValueError('stream_unavailable')
    # No proxy, redirects, cookies or browser-supplied headers/credentials.
    options = {'proxy': None} if 'proxy' in inspect.signature(ws_connect).parameters else {}
    connection = ws_connect(url, additional_headers={'Authorization': 'Bearer ' + key} if key else {},
                            open_timeout=3, max_size=16384, max_queue=8, **options)
    connection.process_redirect = lambda exc: exc  # never follow to a new destination
    ws = await connection
    if ws.response.status_code != 101:
        await ws.close()
        raise ValueError('stream_unavailable')
    await ws.send(json.dumps({'type': 'start', 'sample_rate': RATE, 'format': 'pcm_s16le',
                              'model': p.get(cfg, 'voice.asr.model')}))
    return ws


class Call:
    def __init__(self, owner, ident, emit, submit, cancel, live, transport):
        self.owner, self.id = owner, ident
        self.emit, self.submit, self.cancel, self.live = emit, submit, cancel, live
        self.transport = transport
        self.q = asyncio.Queue(QUEUE)
        self.seq = self.samples = 0
        self.started = time.monotonic()
        self.closed = self.finishing = self.submitted = False
        self.speech = False
        self.silence = 0
        self.pending = ''
        self.sentence = self.reply_chars = 0
        self.tasks = []
        self.ws = None

    async def event(self, kind, **kw):
        if not self.closed and self.live():
            await self.emit({'t': 'call_event', 'id': self.id, 'kind': kind, **kw})

    async def start(self):
        self.ws = await self.transport()
        self.tasks = [asyncio.create_task(self.writer()), asyncio.create_task(self.reader()),
                      asyncio.create_task(self.watch())]
        await self.event('started')

    async def watch(self):
        while not self.closed:
            await asyncio.sleep(.1)
            if not self.live() or time.monotonic() - self.started > (120 if self.submitted else SECONDS + 10):
                await self.stop('expired')

    async def packet(self, obj):
        if self.closed or not self.live() or obj.get('id') != self.id:
            raise ValueError('stale_call')
        if set(obj) != {'t', 'id', 'seq', 'pcm'} or obj['t'] != 'call_pcm':
            raise ValueError('packet')
        if type(obj['seq']) is not int or obj['seq'] != self.seq:
            raise ValueError('sequence')
        if not isinstance(obj['pcm'], str) or len(obj['pcm']) > 8600:
            raise ValueError('size')
        try:
            pcm = base64.b64decode(obj['pcm'], validate=True)
        except (ValueError, TypeError):
            raise ValueError('pcm') from None
        if not pcm or len(pcm) > CHUNK or len(pcm) % 2:
            raise ValueError('size')
        if self.finishing:  # bounded in-flight chunks after VAD; no longer forwarded to ASR
            self.seq += 1
            return
        self.samples += len(pcm) // 2
        if self.samples > RATE * SECONDS or time.monotonic() - self.started > SECONDS:
            raise ValueError('duration')
        values = struct.unpack('<' + 'h' * (len(pcm)//2), pcm)
        voiced = math.sqrt(sum(v*v for v in values) / len(values)) > 350
        self.speech |= voiced
        self.silence = 0 if voiced else self.silence + len(values)
        try:
            self.q.put_nowait(pcm)
        except asyncio.QueueFull:
            raise ValueError('backpressure') from None
        self.seq += 1
        if self.speech and self.silence >= RATE:  # simple one-second energy VAD
            await self.finish()

    async def finish(self):
        if self.closed or self.finishing:
            return
        self.finishing = True
        await self.event('input_done')
        try:
            self.q.put_nowait(None)
        except asyncio.QueueFull:
            await self.stop('backpressure')

    async def writer(self):
        try:
            while not self.closed:
                pcm = await self.q.get()
                await asyncio.wait_for(self.ws.send(json.dumps({'type': 'finish'}) if pcm is None else pcm), 3)
                if pcm is None:
                    return
        except Exception:
            await self.stop('stream_unavailable')

    async def reader(self):
        try:
            while not self.closed:
                raw = await asyncio.wait_for(self.ws.recv(), 35)
                if not isinstance(raw, str) or len(raw) > 16384:
                    raise ValueError()
                msg = json.loads(raw)
                if set(msg) != {'type', 'text'} or msg['type'] not in ('partial', 'final'):
                    raise ValueError()
                text = msg['text']
                if not isinstance(text, str) or len(text) > 4000:
                    raise ValueError()
                if not self.live():
                    return await self.stop('revoked')
                if msg['type'] == 'final':
                    if not self.finishing or self.submitted:
                        raise ValueError()
                    self.submitted = True  # pin before await: at most one main-Agent submission
                    await self.event('final', text=text)
                    if text.strip() and self.speech:
                        await self.submit(text)
                    await self.ws.close()
                    return
                await self.event('partial', text=text)
        except Exception:
            await self.stop('stream_unavailable')

    async def reply(self, text, final=False):
        if self.closed or not self.submitted or not self.live():
            return
        self.pending += text
        self.reply_chars += len(text)
        if len(self.pending) > 8000 or self.reply_chars > 16000 or self.sentence >= 64:
            return await self.stop('reply_limit')
        parts = re.split(r'(?<=[。！？.!?\n])', self.pending)
        self.pending = parts.pop()
        if final and self.pending:
            parts.append(self.pending)
            self.pending = ''
        for part in parts:
            if part.strip():
                self.sentence += 1
                await self.event('sentence', text=part, seq=self.sentence)
        if final:
            await self.event('reply_done')

    async def stop(self, reason='stopped'):
        if self.closed:
            return
        self.closed = True
        await self.emit({'t': 'call_event', 'id': self.id, 'kind': 'stopped', 'reason': reason})
        for task in self.tasks:
            if task is not asyncio.current_task():
                task.cancel()
        if self.ws:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.ws.close(), 1)
        while not self.q.empty():
            self.q.get_nowait()
        self.pending = ''
        if self.submitted:
            await self.cancel()


class Manager:
    def __init__(self, host):
        self.host, self.calls = host, {}

    def available(self):
        # 0342 owner scope: full streaming voice awaits a safety-boundary decision.
        # Keep the prototype source; no service environment can activate this release.
        return False

    def experimental_available(self):
        if os.environ.get('AGENTJ_VOICE_STREAM') != '1':
            return False
        try:
            endpoint(self.host.preferences)
            return True
        except ValueError:
            return False

    def live(self, s):
        h = self.host
        return h._paired_live(s) and not h.stopped() and not h.seat_refusal() and h.agent is not None

    async def handle(self, s, obj):
        h, ident = self.host, obj.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{22}', ident):
            return
        call = self.calls.get(s.cid)
        try:
            if not self.available() or not self.live(s):
                raise ValueError('stream_unavailable')
            if obj['t'] == 'call_start' and set(obj) == {'t', 'id'}:
                # One call per host, no remote device can interrupt another device's call.
                if any(not c.closed and cid != s.cid for cid, c in self.calls.items()):
                    raise ValueError('busy')
                if call:
                    await call.stop('interrupted')
                agent = h.agent
                seat = getattr(h, 'seat', None)
                cfg = h.preferences
                async def submit(text):
                    await h.on_say(s, {'t': 'say', 'sid': ident, 'text': text})
                async def cancel():
                    await h.sends.cancel(s.device, ident)
                    cur = getattr(agent, 'cur_send', None)
                    if h.agent is agent and cur is not None and cur.device == s.device and cur.sid == ident:
                        await agent.halt(clear_queue=False)
                call = Call(s.device, ident, lambda m: h.send_app(s, m), submit, cancel,
                            lambda: self.live(s) and h.agent is agent and getattr(h, 'seat', None) is seat
                            and h.preferences is cfg and self.calls.get(s.cid) is call,
                            lambda: connect(h.preferences))
                self.calls = {cid: c for cid, c in self.calls.items() if not c.closed}
                self.calls[s.cid] = call
                await call.start()
            elif call and call.id == ident:
                if obj['t'] == 'call_pcm':
                    await call.packet(obj)
                elif obj['t'] in ('call_finish', 'call_stop') and set(obj) == {'t', 'id'}:
                    await (call.finish() if obj['t'] == 'call_finish' else call.stop())
                else:
                    raise ValueError('packet')
            else:
                raise ValueError('stale_call')
        except Exception:
            if call:
                await call.stop('rejected')
            await h.send_app(s, {'t': 'call_event', 'id': ident, 'kind': 'error', 'reason': 'stream_unavailable'})

    async def drop(self, cid):
        call = self.calls.pop(cid, None)
        if call:
            await call.stop('disconnected')

    async def reply(self, send, text='', final=False):
        if send is None:
            return
        for call in list(self.calls.values()):
            if call.owner == getattr(send, 'device', None) and call.id == getattr(send, 'sid', None):
                await call.reply(text, final)
