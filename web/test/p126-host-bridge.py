"""JSONL adapter for the existing real Host fixture; no Controls/Host.ask/UV/key-card mocks.

P108_HOST_TREE is an immutable committed dependency tree unpacked by Olares.
Only profile metadata and already-published local state are synthetic; no model/network runs.
"""
import asyncio,base64,json,os,pathlib,sys,tempfile,time
from types import SimpleNamespace
ROOT=pathlib.Path(os.environ.get('P108_HOST_TREE',pathlib.Path(__file__).resolve().parents[3]))/'agentjarvis'
sys.path[:0]=[str(ROOT/'host/tests'),str(ROOT/'host')]
import _hermetic
import test_l1 as l1
from _p541_device import _owner_host
from agentj import wire,approvals,cloud
from agentj.bots.owner import Manager
from agentj.seat_api.runtime import Runtime
from test_p57_passkey import Authenticator,pk_record
from cryptography.hazmat.primitives import serialization
os.environ['AGENTJ_TEST_PASSKEY_ORIGIN']='loopback'
async def main():
 with tempfile.TemporaryDirectory(prefix='p126-browser-host-')as td:
  host=None;manager=None;sent=[]
  while raw:=await asyncio.to_thread(sys.stdin.readline):
   req=json.loads(raw)
   try:
    if req['op']=='setup':
     if host:
      host.stopping.set()
      for task in list(host.__dict__.get('_bg_tasks',())):task.cancel()
      if manager.api_store:manager.api_store.db.close()
      if manager.store:manager.store.close()
     st=l1._state(str(pathlib.Path(td)/str(req['serial'])));cfg=st.config();cfg['channel']=req['channel'];st.config_path.write_text(json.dumps(cfg))
     pub=wire.unb64u(req['pub']);did=st.add_device(pub,'Synthetic API remote',wire.unb64u(req['sk']));assert did==req['device']
     sent=[];host=_owner_host(st,sent);host.channel=req['channel'];host.ask_ttl=60
     session=l1._ready(host,SimpleNamespace(pub=pub,did=did));session.p33=True
     manager=Manager(host);host.bots=manager
     # Real journal binding; no endpoint listener or public route is started.
     cloud.write_cloud(st,{'api':'http://127.0.0.1:1','host_id':'fixture-host','tenant':{'slug':'sample','name':'Sample'},'linked_at':1,'last_seq':0})
     manager.api_runtime=Runtime(manager)
     bid=manager.state().create({'slug':'api-browser-fixture','title':'Synthetic API bot','enabled':bool(req.get('published')),'terms_accepted':True,'provider':{'source':'profile','id':'fixture-profile'}})['id']
     api=manager.api_state();api.profile_metadata=lambda _:{'base_url':'https://api.example.com','model':'fixture-model','api':'openai'}
     endpoint=api.create('browser-endpoint',bid)
     if req.get('published'):
      api.switch(True);api.publish(endpoint['id'],True)
     auth=Authenticator();st.set_passkey(pub,pk_record(auth,rp='localhost'))
     private=auth.key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
     result={'bot':bid,'endpoint':endpoint['id'],'credential':{'credentialId':base64.b64encode(auth.id).decode(),'privateKey':base64.b64encode(private).decode(),'rpId':'localhost','isResidentCredential':True,'userHandle':base64.b64encode(b'0').decode(),'signCount':0},'pk_state':{'t':'pk_state','s':st.passkey_state(did)}}
    elif req['op']=='frame':
     await host._app(session,req['frame']);await asyncio.sleep(.05);result={}
    elif req['op']=='drain':await asyncio.sleep(.02);result={}
    elif req['op']=='check_challenge':result={'challenge':{'t':'pk_state','s':st.passkey_state(session.device),**host.pkpeer.check_fields(session)}}
    elif req['op']=='stop':
     if host:host.stopping.set()
     print('P126RPC:'+json.dumps({'ok':True}),flush=True);break
    else:raise ValueError('unknown fixture operation')
    frames=[obj for _,obj in sent];sent.clear();records=approvals.read_log(st)
    result.update(ok=True,frames=frames,pending=len(host.asks),enabled=manager.api_state().enabled(),keys=len(manager.api_state().metadata().get('keys',[])),audits=[approvals.check_record(st,r)for r in records])
    print('P126RPC:'+json.dumps(result,ensure_ascii=False),flush=True)
   except Exception as error:
    import traceback
    print('P126RPC:'+json.dumps({'ok':False,'error':type(error).__name__+': '+str(error),'trace':traceback.format_exc()}),flush=True)
asyncio.run(main())
