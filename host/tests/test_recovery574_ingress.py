import _hermetic
"""Real PeerService ingress and disk state; Host/forward transport are explicit stand-ins."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentj import noise, peer, peer_guard, peer_service, wire
from agentj.state import State

class Ingress(unittest.IsolatedAsyncioTestCase):
    async def test_334_disjoint_categories_and_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            st=State(Path(d)/'state');st.init(relay='ws://127.0.0.1:9')
            host=SimpleNamespace(st=st,lang='en',stopped=lambda:True,peer_broadcast=lambda *_:None,agent_notice=lambda *_:None,push_notify=lambda *_:None)
            now=[1700000000.0]
            svc=peer_service.PeerService(host,clock=lambda:now[0]);svc.keys=peer.PeerKeys(noise.Keypair.generate(),Ed25519PrivateKey.generate())
            friend=peer.PeerKeys(noise.Keypair.generate(),Ed25519PrivateKey.generate())
            svc.store.add_friend(friend.id,wire.b64u(friend.x_pub),wire.b64u(friend.ed_pub),friend.mbox)
            own=[False];svc.same_owner.active=lambda fid:own[0]
            svc.same_owner.forward=AsyncMock(return_value=True)
            svc.index=peer_guard.SecretIndex([])
            def incoming(mid,**extra):
                now[0]+=10
                return dict(t='pmsg',mid=mid*16,thread='f'*16,text='Synthetic local task',ts=int(now[0]*1000),**extra)
            mail=incoming('a',irt='d'*16)
            svc._on_pmsg(friend.id,mail);svc._on_pmsg(friend.id,mail)
            svc._on_pmsg(friend.id,incoming('b',end=True))
            own[0]=True
            svc._on_pmsg(friend.id,incoming('c',irt='e'*16))
            svc._on_pmsg(friend.id,incoming('d'))
            if svc.jobs:await asyncio.gather(*svc.jobs)
            row=svc.store._read('service.json',{})['sum']
            self.assertEqual((row['n_in'],row['n_receipt'],row['n_task']),(1,2,1))
            self.assertEqual(row['n_auto']+row['n_ask'],0)
            self.assertEqual(svc.same_owner.forward.await_count,2)
