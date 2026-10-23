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

async def main():
    root, action, api, pin = sys.argv[1:]
    st=State(Path(root)); st.init() if not st.exists() else None
    if action=='init':
        for name in ('old','new'):
            pub=os.urandom(32)
            st.pair_device(pub,name,os.urandom(32))
            st.set_passkey(pub,{'id':wire.b64u((name.encode()*16)[:32]),'rp':'m.agentj.app','sc':0})
        cloud._write_cloud_file(st,{'api':api,'host_id':wire.b64u(b'h'*16),'tenant':{'slug':'p490-local','name':'Local'},'linked_at':1,'last_seq':0,'via':'code'})
    elif action=='replace':
        rec=next(v for v in st.devices().values() if v['name']=='old')
        assert st.set_passkey(wire.unb64u(rec['pub']),{'id':wire.b64u(os.urandom(32)),'rp':'m.agentj.app','sc':0})
    elif action=='sync':
        notices.official_keys=lambda:[wire.unb64u(pin)]
        h=serve.Host(st,events='jsonl',read_stdin=False)
        await h.credential_revocations.sync()
    print(json.dumps({'channel':st.config()['channel'],'pk':wire.b64u(st.signing_pub()),'devices':[{ 'id':d,'name':v['name'],'credential':v['pk']['id']} for d,v in st.devices().items()], 'epoch':cr.read(st).get('ack_epoch',0)}))

asyncio.run(main())
