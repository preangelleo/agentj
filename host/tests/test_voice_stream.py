import _hermetic
"""Offline synthetic #441 protocol, no models, keys, network or user state."""
import asyncio
import base64
import json
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from agentj.voice_stream import Call, Manager, CHUNK, RATE, QUEUE


class Transport:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed = False
    async def send(self, message):
        self.sent.append(message)
        if isinstance(message, bytes):
            await self.incoming.put(json.dumps({'type':'partial','text':'synthetic'}))
        else:
            await self.incoming.put(json.dumps({'type':'final','text':'synthetic final'}))
    async def recv(self):
        return await self.incoming.get()
    async def close(self):
        self.closed = True


class Streaming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events, self.submissions, self.cancellations = [], [], []
        self.ws = Transport()
        self.authorized = True
        async def emit(m): self.events.append((time.perf_counter(),m))
        async def submit(text): self.submissions.append(text)
        async def cancel(): self.cancellations.append(True)
        async def transport(): return self.ws
        self.call=Call('phone-a','a'*22,emit,submit,cancel,lambda:self.authorized,transport)
        await self.call.start()
    async def asyncTearDown(self):
        await self.call.stop()
        await asyncio.sleep(0)
    def packet(self, **kw):
        return {'t':'call_pcm','id':'a'*22,'seq':self.call.seq,
                'pcm':base64.b64encode(struct.pack('<h',1000)*3200).decode(),**kw}
    async def wait_kind(self, kind):
        for _ in range(100):
            if any(m['kind']==kind for _,m in self.events):return
            await asyncio.sleep(.001)
        self.fail('missing '+kind)
    async def test_stream_timing(self):
        t0=time.perf_counter()
        await self.call.packet(self.packet())
        await self.wait_kind('partial')
        self.assertFalse(self.call.finishing)
        self.assertEqual(self.submissions,[])
        await asyncio.sleep(.01)
        await self.call.finish()
        await self.wait_kind('final')
        await asyncio.sleep(.001)
        self.assertEqual(self.submissions,['synthetic final'])
        await self.call.reply('First sentence.')
        await asyncio.sleep(.01)
        await self.call.reply('Last sentence.',True)
        times={m['kind']:t for t,m in self.events if m['kind']!='sentence'}
        sentences=[t for t,m in self.events if m['kind']=='sentence']
        self.assertLess(times['partial'],times['final'])
        self.assertLess(sentences[0],sentences[1])
        print('SYNTHETIC_TIMING_MS '+json.dumps({'pcm_to_partial':(times['partial']-t0)*1000,
              'pcm_to_final':(times['final']-t0)*1000,'pcm_to_first_sentence':(sentences[0]-t0)*1000,
              'pcm_to_last_sentence':(sentences[1]-t0)*1000,'fakeASR':True,'fakeModel':True,'realBrowser':False}))
    async def test_duplicate_finish_submits_once(self):
        await self.call.packet(self.packet());await self.call.finish();await self.call.finish()
        await self.wait_kind('final');await asyncio.sleep(.001)
        self.assertEqual(len(self.submissions),1)
    async def test_interrupt_cancels_and_discards_old_reply(self):
        self.call.submitted=True
        await self.call.reply('Old sentence.')
        await self.call.stop('interrupted');count=len(self.events)
        await self.call.reply('Must never speak.',True)
        self.assertEqual(len(self.events),count)
        self.assertEqual(self.cancellations,[True]);self.assertTrue(self.ws.closed)
        self.assertTrue(self.call.q.empty())
    async def test_cross_call_and_unknown_packet_rejected(self):
        for kw in ({'id':'b'*22},{'t':'call_unknown'},{'extra':1},{'seq':True},{'seq':3}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):await self.call.packet(self.packet(**kw))
    async def test_pcm_bounds(self):
        for pcm in ('', '!!!!', base64.b64encode(b'x').decode(),base64.b64encode(b'x'*(CHUNK+2)).decode()):
            with self.subTest(size=len(pcm)),self.assertRaises(ValueError):await self.call.packet(self.packet(pcm=pcm))
    async def test_duration_bound(self):
        self.call.samples=RATE*30
        with self.assertRaisesRegex(ValueError,'duration'):await self.call.packet(self.packet())
    async def test_queue_bound(self):
        for task in self.call.tasks:task.cancel()
        await asyncio.sleep(0)
        for _ in range(QUEUE):await self.call.packet(self.packet())
        with self.assertRaisesRegex(ValueError,'backpressure'):await self.call.packet(self.packet())
    async def test_energy_vad(self):
        await self.call.packet(self.packet())
        for _ in range(5):
            await asyncio.sleep(.001)
            await self.call.packet(self.packet(pcm=base64.b64encode(b'\0'*6400).decode()))
        self.assertTrue(self.call.finishing)
    async def test_revocation_cleans_without_submit(self):
        self.authorized=False
        await asyncio.sleep(.12)
        self.assertTrue(self.call.closed);self.assertTrue(self.ws.closed)
        self.assertEqual(self.submissions,[])
    async def test_unknown_asr_message_fails_closed(self):
        await self.ws.incoming.put('{"type":"unknown","text":"private"}')
        await self.wait_kind('stopped');self.assertEqual(self.submissions,[])
    async def test_unsolicited_final_never_submits(self):
        await self.ws.incoming.put('{"type":"final","text":"too early"}')
        await self.wait_kind('stopped');self.assertEqual(self.submissions,[])
    async def test_reply_bound(self):
        self.call.submitted=True
        await self.call.reply('x'*8001)
        self.assertTrue(self.call.closed)


class ManagerSeams(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent=[];self.submitted=[];self.cancelled=[]
        async def send(s,m):self.sent.append((s.device,m))
        async def say(s,m):self.submitted.append((s.device,m))
        async def cancel(device,ident):self.cancelled.append((device,ident))
        async def halt(**kw):self.cancelled.append(kw)
        self.h=SimpleNamespace(preferences={},_paired_live=lambda s:s.ready,stopped=lambda:False,
            seat_refusal=lambda:None,agent=SimpleNamespace(cur_send=None,halt=halt),send_app=send,
            on_say=say,sends=SimpleNamespace(cancel=cancel))
        self.manager=Manager(self.h)
        self.a=SimpleNamespace(cid=1,device='a',ready=True)
        self.b=SimpleNamespace(cid=2,device='b',ready=True)
        self.transports=[]
        async def connect(cfg):
            ws=Transport();self.transports.append(ws);return ws
        self.patches=[patch('agentj.voice_stream.connect',connect),patch.object(self.manager,'available',return_value=True)]
        for p in self.patches:p.start()
    async def asyncTearDown(self):
        for cid in list(self.manager.calls):await self.manager.drop(cid)
        for p in self.patches:p.stop()
    async def start(self,s,ident='a'*22):
        await self.manager.handle(s,{'t':'call_start','id':ident})
    async def test_other_device_cannot_interrupt(self):
        await self.start(self.a);await self.start(self.b,'b'*22)
        self.assertFalse(self.manager.calls[1].closed);self.assertNotIn(2,self.manager.calls)
        await self.manager.handle(self.b,{'t':'call_stop','id':'a'*22})
        self.assertFalse(self.manager.calls[1].closed)
    async def test_reply_is_task_and_device_bound(self):
        await self.start(self.a);self.manager.calls[1].submitted=True
        for device,sid in [('b','a'*22),('a','b'*22)]:
            await self.manager.reply(SimpleNamespace(device=device,sid=sid),'Wrong.')
        self.assertFalse(any(m['kind']=='sentence' for _,m in self.sent))
        await self.manager.reply(SimpleNamespace(device='a',sid='a'*22),'Correct.')
        self.assertEqual([(d,m['text']) for d,m in self.sent if m['kind']=='sentence'],[('a','Correct.')])
    async def test_replacement_cancels_old_call(self):
        await self.start(self.a);old=self.manager.calls[1];old.submitted=True
        await self.start(self.a,'b'*22)
        self.assertTrue(old.closed);self.assertIn(('a','a'*22),self.cancelled)
    async def test_unpaired_rejected(self):
        self.a.ready=False;await self.start(self.a)
        self.assertEqual(self.transports,[])
    async def test_seat_stop_and_agent_change_cleanup(self):
        for change in ('seat','stop','agent','preferences'):
            with self.subTest(change=change):
                self.h.seat_refusal=lambda:None;self.h.stopped=lambda:False
                await self.start(self.a);call=self.manager.calls[1]
                if change=='seat':self.h.seat_refusal=lambda:'no_seat'
                if change=='stop':self.h.stopped=lambda:True
                if change=='agent':self.h.agent=SimpleNamespace(cur_send=None)
                if change=='preferences':self.h.preferences={}
                await asyncio.sleep(.12);self.assertTrue(call.closed)
    async def test_disconnect_cleanup(self):
        await self.start(self.a);call=self.manager.calls[1]
        await self.manager.drop(1);self.assertTrue(call.closed);self.assertEqual(self.manager.calls,{})

if __name__=='__main__':unittest.main()
