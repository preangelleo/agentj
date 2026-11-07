"""Isolated real candidate Host behind JSONL IPC; Noise/browser transport stays in JS."""
import sys, os, pathlib, asyncio, json, tempfile, base64
from types import SimpleNamespace
root=pathlib.Path(os.environ['AGENTJ_572_HOST_TREE'])
sys.path.insert(0,str(root/'host/tests'))
sys.path.insert(0,str(root/'agentjarvis/host'))
import _hermetic
from agentj import wire, approval_context, approvals
import test_l1 as l1
from test_p57_passkey import Authenticator,pk_record
from cryptography.hazmat.primitives import serialization
os.environ['AGENTJ_TEST_PASSKEY_ORIGIN']='loopback'
def out(x):print('572RPC:'+json.dumps(x,ensure_ascii=False),flush=True)
async def main():
 with tempfile.TemporaryDirectory(prefix='572-real-host-') as td:
  task=None
  while True:
   raw=await asyncio.to_thread(sys.stdin.readline)
   if not raw:break
   req=json.loads(raw)
   try:
    if req['op']=='setup':
     st=l1._state(td);cfg=st.config();cfg['channel']=req['channel'];st.config_path.write_text(json.dumps(cfg));pub=wire.unb64u(req['pub']);did=st.add_device(pub,'synthetic-browser',wire.unb64u(req['sk']))
     assert did==req['device']
     auth=Authenticator();st.set_passkey(pub,pk_record(auth,rp='localhost'))
     sent=[];host=l1._host(st,sent);host.channel=req['channel'];session=l1._ready(host,SimpleNamespace(pub=pub,did=did));host.ask_ttl=60
     host.agent=SimpleNamespace(kind='claude',status='idle',cur_send=SimpleNamespace(source={'kind':'phone','role':'owner'}))
     turn=host.hist.add({'k':'phone','text':'请整理旧导出，保留本月数据。'})
     host.agent.cur_send.approval_origin=approval_context.remember(host,turn,{'kind':'phone','role':'owner'})
     key=auth.key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
     out({'ok':True,'credential':{'credentialId':base64.b64encode(auth.id).decode(),'privateKey':base64.b64encode(key).decode(),'rpId':'localhost','isResidentCredential':True,'userHandle':base64.b64encode(b'0').decode(),'signCount':0}})
    elif req['op']=='card':
     task=asyncio.create_task(host.ask('Bash',{'command':'echo actual-host-572-no-execution'},require_passkey=True))
     for _ in range(1000):
      cards=[o for _,o in sent if o.get('t')=='ask']
      if cards or task.done():break
      await asyncio.sleep(.005)
     assert cards and not task.done()
     card=cards[-1];assert card['approval_v']==2 and card['fa_ch']!=card['envelope_fa_ch'];out({'ok':True,'card':card})
    elif req['op']=='answer':
     obj=req['answer'];negative=[]
     for bad,why in [({k:v for k,v in obj.items() if k!='fa'},'passkey_required'),({**obj,'sig':wire.b64u(bytes(64))},'bad_signature')]:
      await host._app(session,bad);assert not task.done(),'premature completion';assert sent[-1][1].get('why')==why,repr(sent[-1][1]);negative.append(why)
     await host._app(session,obj)
     result=await asyncio.wait_for(task,5);assert result['behavior']=='allow'
     record=approvals.read_log(st)[-1];assert approvals.check_record(st,record)=='ok'
     out({'ok':True,'behavior':result['behavior'],'negative':negative,'audit':'ok','approval_v':record.get('approval_v')})
    elif req['op']=='stop':
     if task and not task.done():task.cancel()
     out({'ok':True});break
   except Exception as e:
    import traceback
    out({'ok':False,'error':type(e).__name__+': '+str(e),'trace':traceback.format_exc()})
asyncio.run(main())
