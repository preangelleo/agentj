"""P490: owner-driven, resumable new→verify→retire wizard, on this host only.
A restore never proves a new channel. Verification is a signed control + fresh WebAuthn
assertion on a genuinely new pairing, after it has resumed. No agent-driven reset.
"""
import json
import re
import time
from . import controls, passkey, wire, credential_revoke as cr

ACTIONS=('security_begin','security_verify','security_retire')
TYPES=('security_status',)+ACTIONS

def read(st):
    p=st.root/'security_rotation.json'
    if not p.exists(): return None
    if p.stat().st_size>64000: raise ValueError('rotation_journal')
    return json.loads(p.read_text())

def save(st,j):
    st.write_private(st.root/'security_rotation.json',json.dumps(j).encode())

def view(st,me=None):
    j=read(st)
    devs=st.devices()
    return {'stage':'verify' if j and not j.get('done') else 'done' if j else 'new',
            'started_at':j['started_at'] if j else None,
            'verified':[d for d in (j or {}).get('verified',[]) if d in devs],
            'old':[d for d,v in devs.items() if j and is_old(j,d,v)],
            'me':me,'can_verify':bool(j and me in devs and not is_old(j,me,devs[me]) and st.passkey_of_device(me))}

def is_old(j, did, rec):
    return did in j['old'] or rec.get('pair_origin',did) in j.get('old_origins',j['old'])

class RotationError(Exception): pass

class Rotation:
    def __init__(self,h): self.h=h
    async def on_phone(self,s,obj):
        try: await self._on_phone(s,obj)
        except RotationError as e:
            await self.h._ctl_res(s,obj.get('r'),obj.get('t'),False,why=str(e),rotation=view(self.h.st,s.device))

    async def _on_phone(self,s,obj):
        h=self.h;action=obj.get('t');r=obj.get('r')
        if not wire.is_rid(r): return
        async def res(ok,**kw):
            await h._ctl_res(s,r,action,ok,**kw,rotation=view(h.st,s.device))
        if action=='security_status': return await res(True)
        if action not in ACTIONS: return await res(False,why='shape')
        j=read(h.st);epoch=j['started_at'] if j else 0
        target={'epoch':obj.get('epoch'),'keep':obj.get('keep')}
        if type(target['epoch']) is not int or target['epoch']!=epoch: return await res(False,why='changed')
        keep=target['keep']
        if not isinstance(keep,list) or len(keep)>10 or not all(isinstance(d,str) and re.fullmatch(r'[-A-Za-z0-9_]{16}',d) for d in keep): return await res(False,why='shape')
        pk=h.st.passkey_of_device(s.device)
        if not pk: return await res(False,why='no_passkey')
        n=obj.get('n')
        if not isinstance(n,str) or not re.fullmatch(r'[0-9a-f]{32}',n): return await res(False,why='shape')
        ch=passkey.elevate_challenge(h.channel,s.device,n,action,n,controls.object_digest(action,target))
        if obj.get('fa') is None: return await res(False,why='passkey_required',fa=pk['id'],fa_ch=wire.b64u(ch))
        why=controls.check(h.st,h.nonces,h.channel,s.device,obj,action,target)
        if not why:
            try:
                fields=passkey.approval_fields(obj['fa'])
                if fields['id']!=pk['id']: raise ValueError('credential')
                h.st.passkey_count(s.device,pk['id'],passkey.verify_assertion(pk,fields,ch))
            except (ValueError,passkey.PasskeyError): why='passkey_failed'
        if why: return await res(False,why=why)
        victims=[]
        with h.st.devices_lock():
            # Re-read under lock: two operators cannot retire each other's last channel.
            j=read(h.st);devs=h.st.devices()
            if s.device not in devs or (j['started_at'] if j else 0)!=epoch: raise RotationError('changed')
            if action=='security_begin':
                if j and not j.get('done'): raise RotationError('in_progress')
                j={'started_at':int(time.time()*1000),'old':list(devs),'old_origins':[v.get('pair_origin',d) for d,v in devs.items()],'old_fingerprints':[cr.fingerprint(v['pk']['id']) for v in devs.values() if isinstance(v.get('pk'),dict)],'verified':[],'done':False}
                save(h.st,j)
            elif action=='security_verify':
                if not j or j.get('done') or is_old(j,s.device,devs[s.device]) or s.mode!='resume' or cr.fingerprint(pk['id']) in j['old_fingerprints']:
                    raise RotationError('new_pairing_required')
                if s.device not in j['verified']: j['verified'].append(s.device)
                save(h.st,j)
            else:
                if (not j or not keep or s.device not in keep or any(d not in devs or d not in j['verified'] or is_old(j,d,devs[d]) for d in keep)):
                    raise RotationError('verified_channel_required')
                # Restores keep their pairing origin even after changing credential or device id.
                victims=[d for d,v in devs.items() if is_old(j,d,v) and d not in keep]
                tomb=cr.read(h.st)
                local=set(tomb.get('local_fingerprints',[]))|set(j['old_fingerprints'])
                for did in victims:
                    p=devs[did].get('pk')
                    if p: local.add(cr.fingerprint(p['id']))
                tomb['local_fingerprints']=sorted(local);tomb['devices']=sorted(set(tomb['devices'])|set(victims))
                tomb['cutoff']=int(time.time()*1000)
                h.st.write_private(h.st.root/'credential_revocations.json',json.dumps(tomb).encode())
                h.st.write_private(h.st.devices_path,json.dumps({d:v for d,v in devs.items() if d not in victims}).encode())
                j['done']=True;save(h.st,j)
        for did in victims:
            h.st.note_removed(did,'revoked');await h._drop_device(did,'security_rotation',True)
        h.st.log('security_rotation',actor=s.device,action=action,result='applied',count=len(victims))
        controls.log(h.st,action=action,device=s.device,result='applied',obj=target,sig=obj['sig'],n=obj['n'],ts=obj['ts'])
        await res(True)
