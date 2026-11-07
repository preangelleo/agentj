"""The actual Host send lock/fragment path rechecks the internal download claim."""
import _hermetic
import asyncio,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from agentj import serve,wire
import test_578_direct_files as fixtures
from test_feedback574_paired import sign

class Delivery(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        f=fixtures.DirectFiles();await f.asyncSetUp();self.addCleanup(f.doCleanups)
        async def upload(*args):return 'https://relay.example/file'
        f.downloads.direct.upload=upload
        await f.ready()
        f.session.p33=False;f.session.state='ready';f.session.pub=b'synthetic-pair-key'
        f.encrypted=[];f.entered=asyncio.Event()
        def encrypt(ad,plain):f.encrypted.append(wire.unpad_json(plain));return b'synthetic-ciphertext'
        f.session.send=NS(encrypt=encrypt)
        f.host.send_lock=asyncio.Lock();f.host.sessions={f.session.cid:f.session};f.st.is_allowed=lambda pub:f.allowed
        async def op(*args):pass
        f.host._op=op
        async def send(session,obj):
            f.entered.set();return await serve.Host.send_app(f.host,session,obj)
        f.host.send_app=send
        return f
    async def send_ready(self,f):
        req=f.req();obj=sign(f.host,f.session,f.key,'private_file_get',req,request=req)
        await f.downloads.on_phone(f.session,obj)
    async def test_valid_ready_key_is_delivered_and_ticket_is_removed(self):
        f=await self.fixture();await self.send_ready(f)
        self.assertEqual(len(f.encrypted),1);self.assertIn('key',f.encrypted[0])
        self.assertEqual(f.downloads.direct.deliveries,{})
    async def test_queued_authority_source_key_transfer_and_claim_changes_deliver_no_key(self):
        for change in ('unpair','estop','seat','identity','expiry','source','signer','key','transfer','prefix','session','claim','offer','record','history','manager'):
            with self.subTest(change=change):
                f=await self.fixture();direct=f.downloads.direct;fid=f.card['id'];row=f.downloads.offers[fid];claim=direct.claims[fid]
                await f.host.send_lock.acquire();task=asyncio.create_task(self.send_ready(f))
                await asyncio.wait_for(f.entered.wait(),1)
                if change=='unpair':f.allowed=False
                elif change=='estop':f.host.stopped=lambda:True
                elif change=='seat':f.host.seat_refusal=lambda:'suspended'
                elif change=='identity':f.host.identity_ok=False
                elif change=='expiry':row['until']=0
                elif change=='source':f.path.write_bytes(b'%PDF-1.7 changed')
                elif change=='signer':f.st.sign_key=lambda device:b'x'*32
                elif change=='key':claim['key']=b'x'*32
                elif change=='transfer':claim['transfer']='b'*32
                elif change=='prefix':claim['nonce_prefix']=b'x'*8
                elif change=='session':claim['session']=NS(device='phone',cid=2)
                elif change=='claim':direct.claims[fid]=dict(claim)
                elif change=='offer':f.downloads.offers[fid]=dict(row)
                elif change=='record':row['rec']['parts']=['other.pdf']
                elif change=='history':f.page['reply']['text']='replaced'
                elif change=='manager':f.host.private_files=NS(direct=direct)
                f.host.send_lock.release();await asyncio.wait_for(task,1)
                self.assertEqual(f.encrypted,[]);self.assertEqual(direct.deliveries,{})
    async def test_fake_wire_guard_cannot_authorize_key_response(self):
        f=await self.fixture()
        result=await f.host.send_app(f.session,{'t':'private_file_res','key':'synthetic','nonce_prefix':'synthetic','delivery_ok':True})
        self.assertFalse(result);self.assertEqual(f.encrypted,[])
    async def test_fragment_boundary_rechecks_after_first_authorized_fragment(self):
        f=await self.fixture();f.session.p33=True
        async def op(*args):f.allowed=False
        f.host._op=op
        with patch.object(wire,'frag_split',return_value=[{'t':'frag','i':0},{'t':'frag','i':1}]):
            await self.send_ready(f)
        self.assertEqual(len(f.encrypted),1)
        self.assertEqual(f.downloads.direct.deliveries,{})
