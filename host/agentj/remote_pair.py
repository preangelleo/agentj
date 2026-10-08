"""P78. Account-authorized pairing; cloud sees ciphertext and bounded public metadata only.
Requests are verified with the existing pinned signer. No unsigned response grants access.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import secrets
import time
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization
from . import cloud, notices, wire, approvals, activity, controls, tasks

CONTEXT = 'agentj-remote-pair-v1'
POLL_CONTEXT = 'agentj-remote-pair-poll-v1'
FIELDS = {'v','id','host','account','seat','channel','op','browser','device','handshake','created_at','expires_at','target'}

def enabled(st):
    try: return st.config().get('remote_pair', True) is not False
    except (OSError, ValueError): return False

def set_enabled(st, on):
    with st.config_lock():
        cfg=st.config(); cfg['remote_pair']=bool(on)
        st.write_private(st.config_path,json.dumps(cfg).encode())
    st.log('remote_pair_switch',status='on' if on else 'off')

def handshake(s): return wire.b64u(hashlib.sha256(s.h).digest()[:16])

def open_request(env, st, now=None, keys=None):
    now=int(time.time()*1000) if now is None else now
    q=notices.opened(env,CONTEXT,keys)
    link=cloud.read_cloud(st)
    if not link or not isinstance(q,dict) or set(q)!=FIELDS or q.get('v')!=1: return None
    if q['host']!=link['host_id'] or q['seat']!=link['host_id'] or q['account']!=link['tenant']['slug'] or q['channel']!=st.config()['channel']: return None
    if any(type(q[k]) is not int for k in ('created_at','expires_at')): return None
    # Newly delivered requests must be fresh, not queued across a power-off. Authorization expires absolutely.
    if not now-15000<=q['created_at']<=now+5000 or not now<q['expires_at']<=q['created_at']+300000: return None
    if not isinstance(q['id'],str) or not cloud._UNBIND_ID.fullmatch(q['id']): return None
    if q['op'] not in ('start','inspect','approve','owner_status','resume','task_on'): return None
    try:
        if len(wire.unb64u(q['browser']))!=32: return None
        X25519PublicKey.from_public_bytes(wire.unb64u(q['browser']))
    except (ValueError,TypeError): return None
    if q['op']=='approve':
        if not isinstance(q['device'],str) or not cloud._DEVICE_ID.fullmatch(q['device']) or not isinstance(q['handshake'],str) or not cloud._UNBIND_ID.fullmatch(q['handshake']): return None
    elif q['device'] is not None or q['handshake'] is not None: return None
    target=q['target']
    if q['op']=='resume':
        if not isinstance(target,dict) or set(target)!={'sha'} or not isinstance(target['sha'],str) or len(target['sha'])!=64: return None
    elif q['op']=='task_on':
        if not isinstance(target,dict) or set(target)!={'id','tsha'} or not isinstance(target['id'],str) or not 1<=len(target['id'])<=64 or not isinstance(target['tsha'],str) or len(target['tsha'])!=64: return None
    elif target is not None: return None
    return q

def stop_sha(st):
    return hashlib.sha256(json.dumps(controls.estop_state(st),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def consume(st,q,now=None):
    now=int(time.time()*1000) if now is None else now
    # Durable before any grant: process restart cannot resurrect a signed request.
    path=st.root/'remote_pair_nonces.json'
    with st.config_lock():
        if path.exists():
            try:
                if path.stat().st_size>128000: return False
                old=json.loads(path.read_text())
                if not isinstance(old,dict): return False
            except (OSError,ValueError): return False
        else: old={}
        old={k:v for k,v in old.items() if type(v) is int and v>now}
        if q['id'] in old or len(old)>=1000: return False
        old[q['id']]=q['expires_at']
        st.write_private(path,json.dumps(old).encode())
    return True

def encrypt_reply(q,data):
    sk=X25519PrivateKey.generate()
    shared=sk.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(q['browser'])))
    aad=(CONTEXT+'\n'+q['id']).encode()
    key=hashlib.sha256(aad+shared).digest(); iv=secrets.token_bytes(12)
    epk=sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    return {'epk':wire.b64u(epk),'iv':wire.b64u(iv),'data':wire.b64u(AESGCM(key).encrypt(iv,json.dumps(data).encode(),aad))}

class RemotePair:
    def __init__(self,host):
        self.host=host; self.results={}; self.seq=0; self.started_at=int(time.time()*1000)

    def reply(self,q,data):
        self.results[q['id']]=(q,encrypt_reply(q,data))

    def allowed(self,q):
        link=cloud.read_cloud(self.host.st)
        return enabled(self.host.st) and link and link['host_id']==q['host'] and link['tenant']['slug']==q['account'] and int(time.time()*1000)<q['expires_at']

    async def execute(self,env):
        h=self.host; q=open_request(env,h.st)
        if q is None or not consume(h.st,q): return
        if q["created_at"] < self.started_at:
            self.reply(q,{"status":"offline"}); return
        if not self.allowed(q): self.reply(q,{'status':'disabled'}); return
        if not h.relay_up: self.reply(q,{'status':'offline'}); return
        if q['op']=='owner_status':
            wd=h.agent_cfg['dir'] if h.agent_cfg else None
            rows=tasks.rows(h.st,wd)[:50]
            self.reply(q,{'status':'owner_status','stopped':h.stopped(),'sha':stop_sha(h.st),
                          'tasks':[{'id':t['id'],'title':t['title'],'enabled':t['enabled'],'tsha':t['tsha'],'problems':t['problems']} for t in rows]})
            return
        if q['op'] in ('resume','task_on'):
            target=q['target']; result='ok'
            if q['op']=='resume':
                if target['sha']!=stop_sha(h.st): result='changed'
                else: await h.do_resume('account:'+q['id'],'账户页 / Account page')
            else:
                try:
                    wd=h.agent_cfg['dir'] if h.agent_cfg else None
                    tasks.set_enabled(h.st,wd,target['id'],True,'account:'+q['id'],target['tsha'])
                    h.scheduler.wake.set()
                except tasks.TaskError as e: result=e.reason
            digest=hashlib.sha256(json.dumps(target,sort_keys=True).encode()).hexdigest()
            approvals.record(h.st,rid=q['id'],agent='account',tool=q['op'],input_sha256=digest,shown_sha256=digest,
                             decision='allow' if result=='ok' else 'deny',reason='account_passkey' if result=='ok' else result,device='account')
            activity.record(h.st,'decision',device='account',summary=q['op']+' / account passkey: '+result,request=q['id'])
            h.st.log('account_owner_approval',request=q['id'],action=q['op'],result=result)
            self.reply(q,{'status':'approved' if result=='ok' else 'gone'})
            return
        if q['op']=='inspect':
            items=[{'device':s.device,'name':s.name,'handshake':handshake(s)} for s in h.sessions.values() if s.state=='pending' and time.monotonic()<s.deadline]
            self.reply(q,{'status':'pending','devices':items}); return
        if q['op']=='approve':
            p=h.pairing; s=h.sessions.get(p.cid) if p and p.cid is not None else None
            if not s or s.state!='pending' or s.device!=q['device'] or handshake(s)!=q['handshake'] or time.monotonic()>=s.deadline:
                self.reply(q,{'status':'gone'}); return
            await self.grant(q,p,s); return
        if h.pairing:
            self.reply(q,{'status':'busy'}); return
        from .serve import Pairing
        ttl=min(300,(q['expires_at']-time.time()*1000)/1000)
        p=Pairing(secrets.token_bytes(16),secrets.token_bytes(32),time.time()+ttl,time.monotonic()+ttl,None,remote=q)
        h.pairing=p; p.timer=asyncio.create_task(h._expire_pairing(p))
        link=wire.pairing_link(h.cfg['web'],h.cfg['relay'],h.channel,h.kp.pub,p.pid,p.psk,int(p.expires))
        svg=wire.pairing_qr(link).png_data_uri(scale=6,border=2,dark='#141414',light='#faf9f5')
        self.reply(q,{'status':'link','link':link,'qr':svg,'expires':int(p.expires)})
        h.st.log('remote_pair_begin',request=q['id'])

    async def pending(self,p,s):
        if not p.remote: return
        if not self.allowed(p.remote) or time.monotonic()>=p.deadline:
            await self.host.close_cid(s.cid,'remote_pair_expired'); return
        await self.grant(p.remote,p,s)

    async def grant(self,q,p,s):
        if not self.allowed(q) or s.state!='pending' or time.monotonic()>=s.deadline: return
        result=await self.host._grant_pair(s,source='account')
        self.host._pair_finish(p,result)
        self.reply(q,{'status':result.get('ev','denied'),'device':s.device,'name':s.name})
        if result.get('ev')!='approved': return
        digest=hashlib.sha256((q['id']+'\n'+s.device+'\n'+handshake(s)).encode()).hexdigest()
        approvals.record(self.host.st,rid=q['id'],agent='account',tool='pair',input_sha256=digest,shown_sha256=digest,decision='allow',reason='account_passkey',device=s.device)
        activity.record(self.host.st,'decision',device=s.device,summary='经账户页添加 / Added from account page',request=q['id'])
        when=time.strftime('%Y-%m-%d %H:%M')
        await self.host.broadcast(f"经账户页添加遥控器 / Remote added from account page ({q['account']}): {s.name} · {when} · {s.device}",frm='notice')
        self.host.push_notify('security')

    def poll(self,link,results):
        self.seq=max(self.seq+1,int(time.time()*1000))
        body={'v':1,'t':'remote_pair','channel':self.host.channel,'ts':int(time.time()),'seq':self.seq,
              'enabled':enabled(self.host.st),'online':self.host.relay_up,'results':results}
        return cloud.post_json(cloud.api_url(self.host.st,link)+'/v1/host/remote-pair',cloud.envelope(POLL_CONTEXT,body,self.host.sk))

    async def run(self):
        from .reporter import in_daemon_thread
        while True:
            try:
                now=int(time.time()*1000)
                self.results={k:v for k,v in self.results.items() if v[0]['expires_at']>now}
                p=self.host.pairing
                if p and p.remote and not self.allowed(p.remote):
                    cid=p.cid; self.host._pair_finish(p,{'ev':'denied','reason':'remote_pair_disabled'})
                    if cid is not None: await self.host.close_cid(cid,'remote_pair_disabled')
                link=cloud.read_cloud(self.host.st)
                if link:
                    results=[{'id':k,'reply':v[1]} for k,v in list(self.results.items())[-4:]]
                    status,obj=await asyncio.wait_for(in_daemon_thread(self.poll,link,results),12)
                    if status==200 and isinstance(obj.get('requests'),list) and len(obj['requests'])<=4:
                        for env in obj['requests']: await self.execute(env)
            except Exception:
                # No exception repr: it may contain pairing material or HTTP data.
                self.host.st.log('remote_pair_poll',status='failed')
            await asyncio.sleep(4)
