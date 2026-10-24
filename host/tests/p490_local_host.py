"""Local workerd closed-loop driver: real Host, State, HTTP signing and revocation.
Only the test control-plane trust root is injected; no authentication result is mocked.
"""
import _hermetic
import asyncio
import json
import os
import sys
from pathlib import Path
from agentj import cloud, wire, serve, notices, credential_revoke as cr
from agentj.state import State
from agentj import passkey
from test_p57_passkey import Authenticator, pk_record
from cryptography.hazmat.primitives import serialization

async def main():
    root, action, api, pin = sys.argv[1:]
    st=State(Path(root)); st.init() if not st.exists() else None
    if action=='init':
        for name in ('old','new'):
            pub=os.urandom(32)
            st.pair_device(pub,name,os.urandom(32))
            auth=Authenticator()
            assert st.set_passkey(pub,pk_record(auth))
            st.write_private(st.root/(name+'-fixture-key.json'),json.dumps({'id':wire.b64u(auth.id),'private':wire.b64u(auth.key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))}).encode())
        cloud._write_cloud_file(st,{'api':api,'host_id':wire.b64u(b'h'*16),'tenant':{'slug':'p490-local','name':'Local'},'linked_at':1,'last_seq':0,'via':'code'})
    elif action=='replace':
        rec=next(v for v in st.devices().values() if v['name']=='old')
        saved=json.loads((st.root/'old-fixture-key.json').read_text());auth=Authenticator()
        auth.id=wire.unb64u(saved['id']);auth.key=serialization.load_der_private_key(wire.unb64u(saved['private']),None)
        oldpk=st.passkey_of_device(wire.device_id(wire.unb64u(rec['pub'])))
        challenge=passkey.replacement_offer_challenge(os.urandom(32),oldpk)
        new=pk_record(Authenticator())
        assert not st.set_passkey(wire.unb64u(rec['pub']),new), 'unsigned replacement must fail'
        assert st.set_passkey(wire.unb64u(rec['pub']),new,replacement=auth.assert_(challenge,b'0',nonce=b'0'),challenge=challenge)
    elif action=='sync':
        notices.official_keys=lambda:[wire.unb64u(pin)]
        h=serve.Host(st,events='jsonl',read_stdin=False)
        await h.credential_revocations.sync()
    print(json.dumps({'channel':st.config()['channel'],'pk':wire.b64u(st.signing_pub()),'devices':[{ 'id':d,'name':v['name'],'credential':v['pk']['id']} for d,v in st.devices().items()], 'epoch':cr.read(st).get('ack_epoch',0)}))

asyncio.run(main())
