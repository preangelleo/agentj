"""Opt-in composed-source E2E: real local workerd, WebSocket host proof and SDK AEAD.

Run only on Olares with P108_REAL_RELAY=1 and the reviewed cloud continuation
merged into the isolated test branch. No real account/provider/network service.
"""
import _hermetic
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from agentj import cloud,wire
from agentj.seat_api import runtime as runtime_mod
from agentj.seat_api.runtime import Runtime
from agentj.bots.store import canonical
import test_p108 as base


@unittest.skipUnless(os.environ.get('P108_REAL_RELAY')=='1','requires composed local relay fixture on Olares')
class RealRelay(unittest.IsolatedAsyncioTestCase):
    setUp=base.ApiTest.setUp
    asyncTearDown=base.ApiTest.asyncTearDown

    async def test_signed_registration_ws_proof_sdk_e2e_and_revocation(self):
        from test_l1 import _state
        import importlib.util
        sdk_path=Path(__file__).resolve().parents[2]/'sdk/python/agentj_seat_api.py'
        spec=importlib.util.spec_from_file_location('real_sdk',sdk_path);sdk=importlib.util.module_from_spec(spec);spec.loader.exec_module(sdk)
        helper=Path(__file__).parent/'fixtures/p108-relay-server.mjs'
        proc=await asyncio.create_subprocess_exec(shutil.which('node') or 'node',str(helper),stdin=asyncio.subprocess.PIPE,
                                                  stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        cloud_server=None;task=None
        try:
            line=await asyncio.wait_for(proc.stdout.readline(),90)
            if not line:raise AssertionError('local relay did not start: '+(await proc.stderr.read()).decode()[-2000:])
            relay=json.loads(line)
            st=_state(str(Path(self.tmp.name)/'registered-host'))
            host=SimpleNamespace(st=st,sk=st.signing_key(),channel=st.config()['channel'],cfg=st.config(),stopped=lambda:False)
            registered=[]
            # Fixed historical relay test issuer, never a production key.
            issuer=Ed25519PrivateKey.from_private_bytes(wire.unb64u('I1QMCvtq_2C7emSnPS9mQxV4YXIWWlfK6k7kREFzb2U'))
            async def registration(reader,writer):
                try:
                    head=await reader.readuntil(b'\r\n\r\n')
                    length=int(next(x.split(b':',1)[1] for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:')))
                    envelope=json.loads(await reader.readexactly(length));inner=json.loads(wire.unb64u(envelope['body']))
                    Ed25519PublicKey.from_public_bytes(wire.unb64u(envelope['pk'])).verify(wire.unb64u(envelope['sig']),
                           ('agentjarvis-host-seat-api-register-v1\n'+envelope['body']).encode())
                    self.assertEqual(inner['credential_epoch'],0);self.assertEqual(inner['endpoint']['id'],self.e['id'])
                    registered.append(inner)
                    claim={'domain':'seat-api-host-v1','endpoint':self.e['id'],'epoch':1,'expires':int(time.time())+59,'host':envelope['pk']}
                    payload=json.dumps({'ticket':{'claim':claim,'sig':wire.b64u(issuer.sign(('agentjarvis/seat-api-ticket-v1\n'+canonical(claim)).encode()))},
                       'account_name':'sample','namespace_version':1,'binding':{'tenant_id':'sample-tenant','host_id':'test-host',
                       'host_pk':envelope['pk'],'credential_epoch':0,'bound_at':1}}).encode()
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(payload)).encode()+b'\r\nConnection: close\r\n\r\n'+payload);await writer.drain()
                finally:writer.close();await writer.wait_closed()
            cloud_server=await asyncio.start_server(registration,'127.0.0.1',0)
            api_url='http://127.0.0.1:'+str(cloud_server.sockets[0].getsockname()[1])
            cloud.write_cloud(st,{'api':api_url,'host_id':'test-host','tenant':{'slug':'sample','name':'Sample'},'linked_at':1,'last_seq':0})
            manager=SimpleNamespace(host=host,api_store=self.api,api_state=lambda:self.api)
            rt=Runtime(manager);rt.service=self.service;self.service.authority=rt.capture;self.service.online=rt.online
            def transport(method,url,body):
                frame={'endpoint':self.e['id'],'epoch':1,'method':method,'path':'_e2e','body':body,'key':None,'idem':None}
                request=urllib.request.Request(relay['httpUrl']+'/__api/'+self.e['id'],data=json.dumps(frame).encode(),headers={'Content-Type':'application/json'},method='POST')
                try:
                    with urllib.request.urlopen(request,timeout=15) as response:return response.status,json.load(response)
                except urllib.error.HTTPError as error:return error.code,json.load(error)
            client=sdk.Client('sample','reply-bot',self.api.public_keys(self.e['id'])['pin'],self.key['secret'],transport=transport)
            with patch.object(runtime_mod,'transport_url',lambda _base,eid:relay['url']+'/seat-api/'+eid+'/h'):
                task=asyncio.create_task(rt.serve(self.e['id']))
                for _ in range(100):
                    try:await asyncio.to_thread(client.refresh);break
                    except sdk.SeatApiError:await asyncio.sleep(.05)
                else:self.fail('real WS host admission never became ready')
                self.assertTrue(registered)
                status,result=await asyncio.to_thread(client.chat,[{'role':'user','content':'REAL_RELAY_BODY_CANARY'}],'real-call','reply-bot')
                self.assertEqual(status,200);self.assertEqual(result['choices'][0]['message']['content'],'API reply');self.assertEqual(self.calls,1)
                self.api.revoke(self.key['id'])
                status,result=await asyncio.to_thread(client.chat,[{'role':'user','content':'revoked'}],'after-revoke','reply-bot')
                self.assertEqual(status,401);self.assertEqual(self.calls,1)
                self.assertNotIn(self.key['secret'],json.dumps(registered))
                self.assertNotIn('REAL_RELAY_BODY_CANARY',json.dumps(registered))
        finally:
            if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
            if cloud_server:cloud_server.close();await cloud_server.wait_closed()
            if proc.returncode is None:
                proc.stdin.write(b'stop\n');await proc.stdin.drain();proc.stdin.close()
                await asyncio.wait_for(proc.wait(),30)
            self.assertEqual(proc.returncode,0,(await proc.stderr.read()).decode()[-2000:])
