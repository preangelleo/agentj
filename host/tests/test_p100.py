"""P100 receive loop regression, real local WebSocket, encrypted frames and sealed signed card answers.
Only disposable paired devices/state and synthetic keys; no external providers.
"""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import ssl
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch
from websockets.asyncio.server import serve as ws_serve
from agentj import controls, noise, serve, wire, provider_profiles as profiles
from test_l1 import _state, Phone
from test_f17 import answer

class CardLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'XDG_CONFIG_HOME':self.tmp.name+'/config'});self.env.start()
        self.st=_state(self.tmp.name);self.host=serve.Host(self.st,events='jsonl',read_stdin=False)
        self.phone=Phone(self.st);self.other=Phone(self.st,name='other');self.frames=asyncio.Queue();self.received=asyncio.Queue();self.connected=asyncio.Event()
        self.tx={};self.rx={}
        for cid,phone in [(11,self.phone),(12,self.other)]:
            key=os.urandom(32);out=os.urandom(32)
            self.tx[cid]=noise.CipherState(key);self.rx[cid]=noise.CipherState(out)
            self.host.sessions[cid]=serve.Session(cid=cid,state='ready',pub=phone.pub,device=phone.did,p33=True,recv=noise.CipherState(key),send=noise.CipherState(out))
        async def relay(ws):
            await ws.send(json.dumps({'t':'challenge','n':wire.b64u(os.urandom(32))}));await ws.recv();await ws.send('{"t":"ok"}');self.connected.set()
            async def output():
                async for data in ws:
                    if isinstance(data,bytes) and data[0]==wire.OP_DATA:
                        cid=int.from_bytes(data[1:5],'big')
                        self.received.put_nowait((cid,wire.unpad_json(self.rx[cid].decrypt(b'',data[6:]),wire.MAX_JSON_P33)))
            async def input():
                while True:await ws.send(await self.frames.get())
            tasks=[asyncio.create_task(output()),asyncio.create_task(input())]
            try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        self.server=await ws_serve(relay,'127.0.0.1',0);self.host.cfg['relay']='ws://127.0.0.1:'+str(self.server.sockets[0].getsockname()[1])
        self.loop=asyncio.create_task(self.host.relay_loop());await asyncio.wait_for(self.connected.wait(),2)
        self.bid=(await self.host.bots.write({'op':'create','config':{'slug':'test-shop'}}))['bot']['id']
    async def asyncTearDown(self):
        await self.host.bots.stop();self.host.stopping.set();self.loop.cancel();await asyncio.gather(self.loop,return_exceptions=True)
        self.server.close();await self.server.wait_closed();self.env.stop();self.tmp.cleanup()
    def signed(self,phone,action,req=None):
        obj={'t':action,'r':os.urandom(8).hex()};target={}
        if req is not None:obj['request']=req;target={'request':req}
        n=os.urandom(16).hex();ts=int(time.time()*1000)
        obj.update(n=n,ts=ts,sig=wire.b64u(phone.sk.sign(controls.signed_message(self.host.channel,phone.did,action,n,ts,controls.object_digest(action,target)))))
        return obj
    async def send(self,obj,cid=11):
        data=bytes([wire.OP_DATA])+cid.to_bytes(4,'big')+bytes([wire.DATA])+self.tx[cid].encrypt(b'',wire.pad_json(obj,wire.MAX_JSON_P33))
        await self.frames.put(data)
    async def wait(self,kind,r=None,cid=11):
        async def receive():
            while True:
                c,m=await self.received.get()
                if c==cid and m.get('t')==kind and (r is None or m.get('r')==r):return m
        return await asyncio.wait_for(receive(),3)
    async def card(self):
        msg=self.signed(self.phone,'bots_write',{'op':'audit_key','id':self.bid});await self.send(msg)
        pending=await self.wait('ctl_pending',msg['r']);self.assertEqual(pending['timeout_ms'],620000)
        return msg,await self.wait('elev')
    async def test_same_connection_save_cancel_other_phone_and_estop(self):
        msg,card=await self.card()
        # Another paired phone shares this exact host relay socket and remains responsive.
        read={'t':'bots_read','r':'other','request':{'op':'list'}};await self.send(read,12);self.assertTrue((await self.wait('bots_res','other',12))['ok'])
        await self.send(answer(self.phone,card,True,'P100_SYNTHETIC_KEY_0123456789'))
        res=await self.wait('ctl_res',msg['r']);self.assertTrue(res['ok'])
        path=self.host.bots.state().secrets(self.bid)/'OPENROUTER_API_KEY';self.assertTrue(path.is_file());self.assertEqual(path.stat().st_mode&0o777,0o600)
        msg,card=await self.card();await self.send(answer(self.phone,card,False));self.assertFalse((await self.wait('ctl_res',msg['r']))['ok']);self.assertFalse(self.host.elevate.cards)
        msg,card=await self.card();stop=self.signed(self.other,'estop');await self.send(stop,12)
        self.assertTrue((await self.wait('ctl_res',stop['r'],12))['ok']);self.assertTrue(self.host.stopped())
        # Terminal bot result may have preceded stop's receipt; task/card state is authoritative here.
        await asyncio.sleep(.05);self.assertFalse(self.host.elevate.cards)
    async def test_malformed_config_returns_receipt_without_dropping_receive_loop(self):
        msg=self.signed(self.phone,'bots_write',{'op':'save','id':self.bid,'config':None})
        await self.send(msg);self.assertFalse((await self.wait('ctl_res',msg['r']))['ok'])
        await self.send({'t':'bots_read','r':'after-malformed','request':{'op':'list'}})
        self.assertTrue((await self.wait('bots_res','after-malformed'))['ok'])

    async def test_replay_busy_detach_and_shutdown(self):
        msg,card=await self.card();await self.send(msg);self.assertEqual((await self.wait('ctl_res',msg['r']))['why'],'replay')
        busy=self.signed(self.phone,'bots_write',{'op':'delete','id':self.bid});await self.send(busy);self.assertEqual((await self.wait('ctl_res',busy['r']))['why'],'busy')
        session=self.host.sessions[11];self.host._detach(11)
        await asyncio.wait_for(asyncio.gather(*list(self.host.bots.phone_tasks)),2)
        self.assertFalse(self.host.elevate.cards);self.assertFalse(self.host.bots.phone_tasks)
        self.assertFalse((self.host.bots.state().secrets(self.bid)/'OPENROUTER_API_KEY').exists())

class Probe(unittest.TestCase):
    def test_categories_do_not_read_error_body_and_only_expose_length_band(self):
        c={'base_url':'https://provider.example/v1','api':'openai','model':'test'}
        class Body:
            def close(self):pass
            def read(self,*a):raise AssertionError('error body read')
        for status,code in [(401,'auth'),(403,'auth'),(404,'model_or_endpoint'),(400,'model'),(429,'rate_limit'),(503,'server'),(302,'redirect')]:
            error=urllib.error.HTTPError('https://PRIVATE_KEY.invalid',status,'PRIVATE_BODY',{},Body())
            with patch('agentj.provider_runtime.public_url',return_value=c['base_url']+'/models'),patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaises(profiles.ProbeError) as caught:profiles.probe(c,'short')
                self.assertEqual(str(caught.exception),'provider_probe_'+code);self.assertEqual(caught.exception.key_length_hint,'0-23')
        for error,code in [(TimeoutError('PRIVATE'),'timeout'),(ssl.SSLError('PRIVATE'),'tls'),(urllib.error.URLError(ssl.SSLError('PRIVATE')),'tls'),(urllib.error.URLError('PRIVATE'),'network')]:
            with patch('agentj.provider_runtime.public_url',return_value=c['base_url']+'/models'),patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect=error
                with self.assertRaisesRegex(profiles.ProbeError,'^provider_probe_'+code+'$'):profiles.probe(c,'x'*32)
        self.assertIsNone(profiles.key_length_hint('x'*32));self.assertEqual(profiles.key_length_hint('x'*5000),'4097+')
    def test_bad_json_and_invalid_response(self):
        c={'base_url':'https://provider.example/v1','api':'openai','model':'test'}
        for raw,code in [(b'PRIVATE_INVALID','bad_json'),(b'{}','invalid_response')]:
            with patch('agentj.provider_runtime.public_url',return_value=c['base_url']+'/models'),patch('urllib.request.build_opener') as opener:
                response=opener.return_value.open.return_value.__enter__.return_value;response.status=200;response.read.return_value=raw
                with self.assertRaisesRegex(profiles.ProbeError,'^provider_probe_'+code+'$'):profiles.probe(c,'x'*32)

class EmbedOrigins(unittest.TestCase):
    def test_exact_https_origins_reject_wildcards_and_whitespace(self):
        from agentj.bots.store import config,BotError
        for origin in ['https://*.example','https://good.example\n','https://good.example/path','http://good.example']:
            with self.assertRaisesRegex(BotError,'invalid_origins'):config({'slug':'test-shop','embed_origins':[origin]})
        self.assertEqual(config({'slug':'test-shop','embed_origins':['https://good.example']})['embed_origins'],['https://good.example'])
