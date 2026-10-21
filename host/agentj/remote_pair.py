"""P78. Account-authorized pairing; cloud sees ciphertext and bounded public metadata only.
Requests are verified with the existing pinned signer. No unsigned response grants access.

P221 (ADR-P221, ARCHITECTURE): the remote pairing password (gate.py) as the second way an account-page start is authorized,
for a session that has no fresh passkey (an email-code sign-in). `pass_hello` hands the browser a one-use X25519 key of
this host (encrypted to the browser key like every reply); `pass_start` carries the password AES-GCM-sealed to that key
(AAD binds the nonce and this host). Only this host decrypts and checks it (scrypt + the persisted failure ledger / lock);
the key lives in memory, is popped on first use and dies after PASS_TTL — the account service only ever relays and stores
ciphertext it cannot open, and a stored copy cannot be opened or replayed later (no offline guessing).
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
from .text import pick
from . import cloud, gate, notices, wire, approvals, activity, controls, tasks

CONTEXT = 'agentj-remote-pair-v1'
POLL_CONTEXT = 'agentj-remote-pair-poll-v1'
MAX_SKEW = 300000   # ms; = the account service's envelope window (hostapi MAX_SKEW_S)
FIELDS = {'v','id','host','account','seat','channel','op','browser','device','handshake','created_at','expires_at','target'}
OPS = ('start','inspect','approve','owner_status','resume','task_on','device_name','cancel','pass_hello','pass_start')
PASS_CONTEXT = 'agentj-remote-pass-v1'
PASS_TTL = 120000   # ms a pass_hello key stays usable
PASS_KEYS = 4       # at most this many outstanding pass_hello keys

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
    q,why=check_request(env, st, now, keys)
    return None if why else q

def check_request(env, st, now=None, keys=None):
    """(request, None) when it may run; (request, 'clock') when it is signed, ours and well-formed but its time does not fit
    this host's clock (P221: answered with a reason, never dropped in silence); (None, None) otherwise."""
    now=int(time.time()*1000) if now is None else now
    q=notices.opened(env,CONTEXT,keys)
    link=cloud.read_cloud(st)
    if not link or not isinstance(q,dict) or set(q)!=FIELDS or q.get('v')!=1: return None,None
    if q['host']!=link['host_id'] or q['seat']!=link['host_id'] or q['account']!=link['tenant']['slug'] or q['channel']!=st.config()['channel']: return None,None
    if any(type(q[k]) is not int for k in ('created_at','expires_at')): return None,None
    if not isinstance(q['id'],str) or not cloud._UNBIND_ID.fullmatch(q['id']): return None,None
    if q['op'] not in OPS: return None,None
    try:
        if len(wire.unb64u(q['browser']))!=32: return None,None
        X25519PublicKey.from_public_bytes(wire.unb64u(q['browser']))
    except (ValueError,TypeError): return None,None
    if q['op']=='approve':
        if not isinstance(q['device'],str) or not cloud._DEVICE_ID.fullmatch(q['device']) or not isinstance(q['handshake'],str) or not cloud._UNBIND_ID.fullmatch(q['handshake']): return None,None
    elif q['device'] is not None or q['handshake'] is not None: return None,None
    target=q['target']
    if q['op']=='resume':
        if not isinstance(target,dict) or set(target)!={'sha'} or not isinstance(target['sha'],str) or len(target['sha'])!=64: return None,None
    elif q['op']=='task_on':
        if not isinstance(target,dict) or set(target)!={'id','tsha'} or not isinstance(target['id'],str) or not 1<=len(target['id'])<=64 or not isinstance(target['tsha'],str) or len(target['tsha'])!=64: return None,None
    elif q['op']=='device_name':
        if not isinstance(target,dict) or set(target)!={'device','name'} or not isinstance(target['device'],str) or not cloud._DEVICE_ID.fullmatch(target['device']): return None,None
        name=target['name']
        if not isinstance(name,str) or not 1<=len(name.strip())<=64 or any(ord(c)<32 or ord(c)==127 for c in name): return None,None
    elif q['op']=='pass_start':
        if not isinstance(target,dict) or set(target)!={'nonce','epk','iv','data'} or not all(isinstance(target[k],str) for k in target): return None,None
        try:
            if not cloud._UNBIND_ID.fullmatch(target['nonce']) or len(wire.unb64u(target['epk']))!=32 or len(wire.unb64u(target['iv']))!=12 or not 16<len(wire.unb64u(target['data']))<=1100: return None,None
        except (ValueError,TypeError): return None,None
    elif target is not None: return None,None
    if not q['created_at']<q['expires_at']<=q['created_at']+300000: return None,None
    # Newly delivered requests must be fresh, not queued across a power-off. Authorization expires absolutely. `now` is the
    # account service's clock as this host estimates it (RemotePair.now_ms), so a computer whose clock is off still pairs.
    if not now-15000<=q['created_at']<=now+5000 or not now<q['expires_at']: return q,'clock'
    return q,None

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

def pass_aad(nonce,host):
    return (PASS_CONTEXT+'\n'+nonce+'\n'+host).encode()

def open_pass(sk,q,full=False):
    """The password the browser sealed to this host's pass_hello key → str, or None (tampered / not ours)."""
    t=q['target']; aad=pass_aad(t['nonce'],q['host'])
    try:
        shared=sk.exchange(X25519PublicKey.from_public_bytes(wire.unb64u(t['epk'])))
        plain=AESGCM(hashlib.sha256(aad+shared).digest()).decrypt(wire.unb64u(t['iv']),wire.unb64u(t['data']),aad)
        payload=json.loads(plain); pw=payload.get('pass')
    except Exception: return None
    return (payload if full else pw) if isinstance(pw,str) and 0<len(pw)<=gate.MAX_LEN else None

class RemotePair:
    def __init__(self,host):
        self.host=host; self.results={}; self.seq=0; self.started_at=int(time.time()*1000)
        self.offset=0   # P221: account-service clock minus this computer's (ms), learned from every poll; ±MAX_SKEW at most
        self.waiting=None   # P444b: current account QR pending (q, pairing, session), memory only
        self.pass_keys={}   # P221: nonce → (X25519 private key, expires ms); memory only, one use

    def now_ms(self):
        return int(time.time()*1000)+self.offset

    def learn_clock(self,server_now,t0,t1):
        """P221: WSL / a laptop after sleep often runs seconds to minutes off; requests are timed by the account service, so
        judge them by its clock. Bounded by the envelope's own window (the service refuses a poll further off anyway)."""
        if type(server_now) is int and t0<=t1:
            self.offset=max(-MAX_SKEW,min(MAX_SKEW,server_now-(t0+t1)//2))

    def reply(self,q,data):
        self.results[q['id']]=(q,encrypt_reply(q,data))

    def allowed(self,q):
        link=cloud.read_cloud(self.host.st)
        return enabled(self.host.st) and link and link['host_id']==q['host'] and link['tenant']['slug']==q['account'] and self.now_ms()<q['expires_at']

    async def execute(self,env):
        h=self.host; now=self.now_ms(); q,why=check_request(env,h.st,now)
        if why=='clock':
            # P221: signed and ours, but this computer's clock is too far off to judge it: say so (no grant, no QR).
            if consume(h.st,{**q,'expires_at':max(q['expires_at'],now+300000)},now):
                self.reply(q,{'status':'clock'}); h.st.log('remote_pair_clock',request=q['id'],skew_s=int((q['created_at']-now)/1000))
            return
        if q is None:
            h.st.log('remote_pair_ignored')   # P221: not ours / not signed by the account service — no detail, but a trace
            return
        if not consume(h.st,q,now): return
        if q["created_at"] < self.started_at+self.offset:
            self.reply(q,{"status":"offline"}); return
        if not self.allowed(q): self.reply(q,{'status':'disabled'}); return
        if not h.relay_up: self.reply(q,{'status':'offline'}); return
        if q['op']=='pass_hello':
            self.reply(q,self.pass_hello()); return
        if q['op']=='pass_start':
            refusal=await self.pass_check(q)
            if refusal:
                if 'confirm' not in refusal: self.reply(q,refusal); return
                # Password proof is sealed to a one-use host key, including this exact device/handshake.
                d=refusal['confirm']; p=h.pairing
                s=h.sessions.get(p.cid) if p and p.cid is not None else None
                if (not isinstance(d,dict) or set(d)!={'device','handshake'} or not s or s.state!='pending'
                        or s.device!=d['device'] or handshake(s)!=d['handshake'] or time.monotonic()>=s.deadline):
                    self.reply(q,{'status':'gone'}); return
                await self.grant(q,p,s); return
        if q['op']=='owner_status':
            wd=h.agent_cfg['dir'] if h.agent_cfg else None
            rows=tasks.rows(h.st,wd)[:50]
            self.reply(q,{'status':'owner_status','stopped':h.stopped(),'sha':stop_sha(h.st),
                          'tasks':[{'id':t['id'],'title':t['title'],'enabled':t['enabled'],'tsha':t['tsha'],'problems':t['problems']} for t in rows]})
            return
        if q['op'] in ('resume','task_on','device_name'):
            target=q['target']; result='ok'
            if q['op']=='resume':
                if target['sha']!=stop_sha(h.st): result='changed'
                else: await h.do_resume('account:'+q['id'],pick(getattr(h,'lang','zh'),'账户页','Account page'))
            elif q['op']=='device_name':
                result='ok' if h.st.rename_device(target['device'],target['name'],via='account') else 'gone'
                if result=='ok': h.device_renamed(target['device'])   # P179: owner_name; sessions, report, phones follow
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
            items=[self.pending_device(s) for s in h.sessions.values() if s.state=='pending' and time.monotonic()<s.deadline]
            self.reply(q,{'status':'pending','devices':items}); return
        if q['op']=='approve':
            p=h.pairing; s=h.sessions.get(p.cid) if p and p.cid is not None else None
            if not s or s.state!='pending' or s.device!=q['device'] or handshake(s)!=q['handshake'] or time.monotonic()>=s.deadline:
                self.reply(q,{'status':'gone'}); return
            await self.grant(q,p,s); return
        p=h.pairing
        if q['op']=='cancel':
            # P177: the owner (account passkey) ends a pairing slot nobody has connected to yet — an earlier account-page QR
            # or a forgotten `agentj pair` (it is told "cancelled on the account page"). A phone mid-pairing is never cut off.
            if p and p.cid is not None: self.reply(q,self.busy(p)); return
            if p: h._pair_finish(p,{'ev':'denied','reason':'account_cancelled'})
            h.st.log('remote_pair_cancel',request=q['id'],held='remote' if p and p.remote else 'local' if p else 'none')
            self.reply(q,{'status':'cancelled'}); return
        if p:
            # P177: a newer account-page start takes over an earlier account-page QR nobody scanned (the same owner after a
            # reload or an error); a terminal `agentj pair` or a phone mid-pairing is never replaced silently.
            if not (p.remote and p.cid is None): self.reply(q,self.busy(p)); return
            h._pair_finish(p,{'ev':'replaced'}); h.st.log('remote_pair_takeover',request=q['id'])
        from .serve import Pairing
        ttl=min(300,(q['expires_at']-self.now_ms())/1000)
        p=Pairing(secrets.token_bytes(16),secrets.token_bytes(32),time.time()+ttl,time.monotonic()+ttl,None,remote=q)
        # Link and QR first: a failure here must not leave the slot held for five minutes (P177).
        link=wire.pairing_link(h.cfg['web'],h.cfg['relay'],h.channel,h.kp.pub,p.pid,p.psk,int(p.expires))
        svg=wire.pairing_qr(link).png_data_uri(scale=6,border=2,dark='#141414',light='#faf9f5')
        h.pairing=p; p.timer=asyncio.create_task(h._expire_pairing(p))
        self.reply(q,{'status':'link','link':link,'qr':svg,'expires':int(p.expires),'cancel':True})
        h.st.log('remote_pair_begin',request=q['id'])

    def pass_hello(self):
        """P221: whether a remote pairing password is set here (and its lock), plus a fresh one-use key to seal it to."""
        st=self.host.st
        if not gate.is_set(st): return {'status':'pass_hello','set':False}
        now=self.now_ms()
        keys={k:v for k,v in self.pass_keys.items() if v[1]>now}
        while len(keys)>=PASS_KEYS: keys.pop(next(iter(keys)))
        sk=X25519PrivateKey.generate(); nonce=wire.b64u(secrets.token_bytes(16))
        keys[nonce]=(sk,now+PASS_TTL); self.pass_keys=keys
        hpk=sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        return {'status':'pass_hello','set':True,'hpk':wire.b64u(hpk),'nonce':nonce,'locked':gate.lock_left(st),'left':gate.tries_left(st)}

    async def pass_check(self,q):
        """P221: verify the password; return None for start, a confirmed binding, or a refusal status."""
        st=self.host.st
        k=self.pass_keys.pop(q['target']['nonce'],None)
        if not k or k[1]<=self.now_ms(): return {'status':'pass_expired'}
        payload=open_pass(k[0],q,full=True)
        pw=payload.get('pass') if payload else None
        if pw is None:
            st.log('remote_pair_pass',request=q['id'],result='unreadable'); return {'status':'pass_expired'}
        try: await asyncio.to_thread(gate.verify,st,pw)
        except gate.GateError as e:
            st.log('remote_pair_pass',request=q['id'],result=e.reason)
            if e.reason=='wrong':   # the next try comes with a fresh one-use key (no extra round trip)
                nxt=self.pass_hello(); return {**nxt,'status':'pass_wrong','left':e.info.get('left',0)}
            if e.reason=='locked': return {'status':'pass_locked','seconds':e.info.get('seconds') or gate.lock_left(st)}
            return {'status':'pass_not_set'}
        finally: pw=None
        st.log('remote_pair_pass',request=q['id'],result='ok')
        return {'confirm':payload['confirm']} if 'confirm' in payload else None

    @staticmethod
    def busy(p):
        # P177: who holds the slot and until when, so the account page can say it; `cancel` = this host accepts op cancel.
        return {'status':'busy','by':'remote' if p.remote else 'local','phone':p.cid is not None,'until':int(p.expires),'cancel':p.cid is None}

    @staticmethod
    def pending_device(s):
        return {'device':s.device,'name':s.name,'handshake':handshake(s),'code':wire.safety_code(s.h)}

    async def pending(self,p,s):
        if not p.remote: return
        if not self.allowed(p.remote) or time.monotonic()>=p.deadline:
            await self.host.close_cid(s.cid,'remote_pair_expired'); return
        # Update the original encrypted reply: the account page already polls it every second.
        # Scanning alone never grants; the owner compares the SAS then explicitly approves.
        self.waiting=(p.remote,p,s)
        self.reply(p.remote,{'status':'pending','devices':[self.pending_device(s)]})

    async def grant(self,q,p,s):
        if (not self.allowed(q) or s.state!='pending' or time.monotonic()>=s.deadline
                or time.monotonic()>=p.deadline or (p.remote and not self.allowed(p.remote))):
            self.reply(q,{'status':'gone'}); return
        result=await self.host._grant_pair(s,source='account')
        self.host._pair_finish(p,result)
        self.reply(q,{'status':result.get('ev','denied'),'device':s.device,'name':s.name})
        if result.get('ev')!='approved': return
        digest=hashlib.sha256((q['id']+'\n'+s.device+'\n'+handshake(s)).encode()).hexdigest()
        how='account_pass' if q['op']=='pass_start' else 'account_passkey'   # P221: which proof the owner gave
        approvals.record(self.host.st,rid=q['id'],agent='account',tool='pair',input_sha256=digest,shown_sha256=digest,decision='allow',reason=how,device=s.device)
        activity.record(self.host.st,'decision',device=s.device,summary=pick(getattr(self.host,'lang','zh'),'经账户页添加','Added from account page'),request=q['id'])
        when=time.strftime('%Y-%m-%d %H:%M')
        await self.host.broadcast(pick(getattr(self.host,'lang','zh'),f"经账户页添加遥控器（{q['account']}）：{s.name} · {when} · {s.device}",f"Remote added from account page ({q['account']}): {s.name} · {when} · {s.device}"),frm='notice')
        self.host.push_notify('security')

    def refresh_waiting(self):
        if not self.waiting: return
        q,p,s=self.waiting
        if (self.host.pairing is not p or s.state!='pending' or time.monotonic()>=s.deadline
                or time.monotonic()>=p.deadline or not self.allowed(q)):
            self.reply(q,{'status':'approved' if s.state=='ready' else 'gone'})
            self.waiting=None

    def poll(self,link,results):
        self.seq=max(self.seq+1,int(time.time()*1000))
        body={'v':1,'t':'remote_pair','channel':self.host.channel,'ts':int(time.time()),'seq':self.seq,
              'enabled':enabled(self.host.st),'online':self.host.relay_up,'results':results}
        return cloud.post_json(cloud.api_url(self.host.st,link)+'/v1/host/remote-pair',cloud.envelope(POLL_CONTEXT,body,self.host.sk))

    async def run(self):
        from .reporter import in_daemon_thread
        while True:
            try:
                self.refresh_waiting()
                now=self.now_ms()
                self.results={k:v for k,v in self.results.items() if v[0]['expires_at']>now}
                p=self.host.pairing
                if p and p.remote and not self.allowed(p.remote):
                    cid=p.cid; self.host._pair_finish(p,{'ev':'denied','reason':'remote_pair_disabled'})
                    if cid is not None: await self.host.close_cid(cid,'remote_pair_disabled')
                link=cloud.read_cloud(self.host.st)
                if link:
                    results=[{'id':k,'reply':v[1]} for k,v in list(self.results.items())[-4:]]
                    t0=int(time.time()*1000)
                    status,obj=await asyncio.wait_for(in_daemon_thread(self.poll,link,results),12)
                    if status==200 and isinstance(obj,dict): self.learn_clock(obj.get('now'),t0,int(time.time()*1000))
                    if status==200 and isinstance(obj.get('requests'),list) and len(obj['requests'])<=4:
                        for env in obj['requests']: await self.execute(env)
            except Exception:
                # No exception repr: it may contain pairing material or HTTP data.
                self.host.st.log('remote_pair_poll',status='failed')
            await asyncio.sleep(4)
