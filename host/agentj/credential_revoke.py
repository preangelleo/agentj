"""P490. Durable, public credential tombstones; no private credential material.

The journal is written before removing allowlist entries. Every allowlist read filters it,
so a crash between those writes fails closed. Only signed snapshots may advance it.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
import uuid
from . import cloud, notices, wire

CONTEXT = 'agentj-credential-revoke-v1'
POLL_CONTEXT = 'agentj-credential-revoke-poll-v1'
HEX = re.compile(r'[0-9a-f]{64}')
TARGET = re.compile(r'[a-z0-9.-]{1,253}:[0-9a-f]{64}')
MAX_TARGETS = 256
SYNC_TIMEOUT = 12.0

def target(pk):
    return pk.get('rp','m.agentj.app')+':'+fingerprint(pk['id'])


def credential_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1400:
        raise ValueError('credential_id')
    if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
        return wire.b64u(uuid.UUID(value).bytes)
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value):
        raise ValueError('credential_id')
    raw = wire.unb64u(value.rstrip('='))
    if not raw or len(raw) > 1024:
        raise ValueError('credential_id')
    return wire.b64u(raw)


def fingerprint(value):
    return hashlib.sha256(wire.unb64u(credential_id(value))).hexdigest()


def read(st):
    p = st.root / 'credential_revocations.json'
    if not p.exists():
        return {'epoch': 0, 'fingerprints': [], 'devices': []}
    if p.stat().st_size > 128000:
        raise ValueError('revocation_journal')
    j = json.loads(p.read_text())
    if (not isinstance(j, dict) or type(j.get('epoch')) is not int or j['epoch'] < 0
            or not isinstance(j.get('fingerprints'), list) or len(j['fingerprints']) > MAX_TARGETS
            or not all(isinstance(x, str) and TARGET.fullmatch(x) for x in j['fingerprints'])
            or not isinstance(j.get('local_fingerprints',[]),list) or not all(isinstance(x,str) and HEX.fullmatch(x) for x in j.get('local_fingerprints',[]))
            or type(j.get('cutoff',0)) is not int
            or not isinstance(j.get('devices'), list)
            or not all(isinstance(x, str) and cloud._DEVICE_ID.fullmatch(x) for x in j['devices'])):
        raise ValueError('revocation_journal')
    return j


def history(st):
    path=st.root/'credential_history.json'
    if not path.exists(): return {}
    if path.stat().st_size>128000: raise ValueError('credential_history')
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or not all(isinstance(k,str) and TARGET.fullmatch(k) and type(v) is int for k,v in data.items()):
        raise ValueError('credential_history')
    return data


def note_replaced(st, pk):
    rows=history(st);rows[target(pk)]=int(time.time())
    rows=dict(sorted(rows.items(),key=lambda kv:kv[1])[-512:])
    st.write_private(st.root/'credential_history.json',json.dumps(rows).encode())


def ancestors(rec):
    rows=rec.get('pk_ancestors',[]) if isinstance(rec,dict) else []
    if not isinstance(rows,list) or len(rows)>MAX_TARGETS or not all(isinstance(x,str) and TARGET.fullmatch(x) for x in rows):
        raise ValueError('credential_lineage')
    return set(rows)


def blocked(st, did, rec, journal=None):
    j = read(st) if journal is None else journal
    if did in j['devices']:
        return True
    pk = rec.get('pk') if isinstance(rec, dict) else None
    lineage=ancestors(rec)
    if isinstance(pk,dict) and pk.get('id'): lineage.add(target(pk))
    return bool(lineage.intersection(j['fingerprints']) or any(x.rsplit(':',1)[-1] in j.get('local_fingerprints',[]) for x in lineage))


def snapshot(env, st, seq, now, keys=None):
    q = notices.opened(env, CONTEXT, keys)
    link = cloud.read_cloud(st)
    if not link or not isinstance(q, dict) or set(q) != {'v','host','seat','account','channel','seq','nonce','created_at','expires_at','epoch','fingerprints','queries'}:
        return None
    if (q['v'] != 1 or q['host'] != link['host_id'] or q['seat'] != link['host_id']
            or q['account'] != link['tenant']['slug'] or q['channel'] != st.config()['channel']
            or type(q['seq']) is not int or q['seq'] != seq
            or not isinstance(q['nonce'], str) or not cloud._UNBIND_ID.fullmatch(q['nonce'])
            or any(type(q[k]) is not int for k in ('created_at','expires_at','epoch'))
            or not 0 <= q['created_at'] < q['expires_at'] <= q['created_at']+300000 or q['epoch'] < 0
            or not isinstance(q['fingerprints'], list) or len(q['fingerprints']) > MAX_TARGETS
            or len(set(q['fingerprints'])) != len(q['fingerprints'])
            or not all(isinstance(x, str) and TARGET.fullmatch(x) for x in q['fingerprints'])):
        return None
    if not isinstance(q['queries'],list) or len(q['queries'])>1: return None
    for query in q['queries']:
        if (not isinstance(query,dict) or set(query)!={'id','fingerprints'}
                or not isinstance(query['id'],str) or not cloud._UNBIND_ID.fullmatch(query['id'])
                or not isinstance(query['fingerprints'],list) or len(query['fingerprints'])>32
                or not all(isinstance(x,str) and TARGET.fullmatch(x) for x in query['fingerprints'])): return None
    # Wall-clock skew is diagnostic only. Freshness is enforced by sync's
    # persisted request sequence and monotonic deadline, never the host wall clock.
    if not now-300000 <= q['created_at'] <= now+5000:
        st.log('credential_revoke_clock', ms=q['created_at']-now)
    return q


class Revocations:
    def __init__(self, host):
        import asyncio
        self.host = host
        self.seq = int(time.time()*1000)
        self.lock = asyncio.Lock()
        self.results = []

    def poll(self, link, seq):
        h = self.host
        from .security_rotation import view
        stage=view(h.st)
        body = {'rotation':{'stage':stage['stage'],'verified':len(stage['verified']),'old':len(stage['old'])},'v':1, 't':'credential_revoke', 'channel':h.channel, 'ts':int(time.time()),
                'seq':seq, 'epoch':read(h.st).get('ack_epoch',0), 'results':self.results}
        return cloud.post_json(cloud.api_url(h.st,link)+'/v1/host/credential-revoke',
                               cloud.envelope(POLL_CONTEXT,body,h.sk),max_response=64*1024)

    async def sync(self):
        """Required before relay acceptance; signed response binds this exact request, host and account."""
        import asyncio
        from .reporter import in_daemon_thread
        async with self.lock:
            h = self.host; link = cloud.read_cloud(h.st)
            if not link:
                return True
            # Persist before send: a restart/clock rollback cannot reuse an earlier signed response's poll sequence.
            with h.st.devices_lock():
                path=h.st.root/'credential_revoke_seq.json'
                last=json.loads(path.read_text()) if path.exists() else 0
                if type(last) is not int or not 0<=last<cloud.MAX_SEQ: raise ValueError('revocation_seq')
                self.seq=max(self.seq+1,last+1,int(time.time()*1000))
                h.st.write_private(path,json.dumps(self.seq).encode())
            seq = self.seq
            deadline = time.monotonic() + SYNC_TIMEOUT
            code, obj = await asyncio.wait_for(in_daemon_thread(self.poll,link,seq),SYNC_TIMEOUT)
            q = snapshot(obj.get('snapshot'),h.st,seq,int(time.time()*1000)) if code == 200 else None
            if q is None or time.monotonic() >= deadline:
                raise ValueError('revocation_sync')
            try:
                await self.apply(q)
            except Exception:
                for fp in q['fingerprints']:
                    h.st.log('credential_revoke',actor='account',target=q['host'][:8],fingerprint=fp.rsplit(':',1)[-1],phase='failed')
                raise
            self.results = []
            journal=read(h.st)
            for query in q['queries']:
                items=[]
                for fp in query['fingerprints']:
                    current=[did[:8] for did,rec in h.st.devices().items()
                             if isinstance(rec.get('pk'),dict) and target(rec['pk'])==fp]
                    prior=[did[:8] for did,rec in h.st.devices().items() if fp in ancestors(rec)]
                    items.append({'fingerprint':fp.rsplit(':',1)[1],'devices':sorted(set(current+prior)),'status':'matched' if current else
                                  'revoked' if fp in journal['fingerprints'] or fp.rsplit(':',1)[-1] in journal.get('local_fingerprints',[]) else 'historical_overwritten' if prior or fp in history(h.st) else 'unknown_or_overwritten'})
                self.results.append({'id':query['id'],'items':items})
            return True

    async def apply(self, q):
        h = self.host; st = h.st
        # One journal, one allowlist lock. The journal cannot regress, even with overlapping polls/restarts.
        with st.devices_lock():
            old = read(st)
            if q['epoch'] < old['epoch'] or not set(old['fingerprints']).issubset(q['fingerprints']):
                raise ValueError('revocation_regression')
            if q['epoch'] == old['epoch'] and set(old['fingerprints']) != set(q['fingerprints']):
                raise ValueError('revocation_epoch')
            raw = json.loads(st.devices_path.read_text())
            victims = set(old['devices'])
            if q['epoch'] > old['epoch']:
                pending=getattr(h,'pairing',None)
                session=h.sessions.get(pending.cid) if pending and pending.remote and pending.cid is not None else None
                if session and pending.remote.get('epoch',0)<q['epoch']:
                    victims.add(session.device)  # includes a grant suspended after allowlist insertion, before completion
            for did, rec in raw.items():
                if blocked(st,did,rec,{**old,'fingerprints':q['fingerprints']}):
                    victims.add(did)
            st.write_private(st.root/'credential_revocations.json',json.dumps(
                {**old,'epoch':q['epoch'],'fingerprints':q['fingerprints'],'devices':sorted(victims)}).encode())
            # Filtered State.devices() already makes all frames/restore/tickets fail here, before the first await.
            st.write_private(st.devices_path,json.dumps({k:v for k,v in raw.items() if k not in victims}).encode())
        if q['epoch'] > old['epoch']:
            p = getattr(h,'pairing',None)
            if p and p.remote:
                h._pair_finish(p,{'ev':'denied','reason':'credential_revoked'})
            rp = getattr(h,'remote_pair',None)
            if rp:
                rp.pass_keys.clear(); rp.waiting = None
            for a in getattr(h,'asks',{}).values():
                if not a.fut.done(): a.fut.set_result(('deny',None,None,None))
            elev=getattr(h,'elevate',None)
            for card in (getattr(elev,'cards',{}) or {}).values():
                if card.get('cancel'): card['cancel'].set()
                if not card['fut'].done(): card['fut'].set_result({'result':'denied'})
                card['revoked']=True
            h.__dict__.pop('_parked_ok',None)
            for a in getattr(h,'questions',{}).values():
                fut = getattr(a,'fut',None)
                if fut and not fut.done(): fut.set_result(('gone',None,None,None,None))
        cleanup=victims-set(old.get('cleaned',[]))
        cleanup.update(s.device for s in h.sessions.values() if s.device in victims)
        for did in cleanup:
            st.note_removed(did,'revoked')
            await h._drop_device(did,'credential_revoked',True)
        if cleanup:
            with st.devices_lock():
                journal=read(st);journal['cleaned']=sorted(set(journal.get('cleaned',[]))|cleanup)
                st.write_private(st.root/'credential_revocations.json',json.dumps(journal).encode())
        for fp in set(q['fingerprints'])-set(old['fingerprints']):
            st.log('credential_revoke',actor='account',target=q['host'][:8],fingerprint=fp.rsplit(':',1)[-1],phase='applied')
        st.log('credential_revoke',epoch=q['epoch'],result='applied',count=len(victims))
        # ACK is a separate durable commit AFTER allowlist/session/resource cleanup and audit.
        # A journal-first crash must be re-applied, never acknowledged on the next poll as already complete.
        with st.devices_lock():
            journal=read(st);journal['ack_epoch']=q['epoch']
            st.write_private(st.root/'credential_revocations.json',json.dumps(journal).encode())

    async def run(self):
        import asyncio
        while True:
            try: await self.sync()
            except Exception: self.host.st.log('credential_revoke_sync',status='failed')
            await asyncio.sleep(4)


def require_compatible_version(st, version):
    """Security journals are monotonic: never install a host that ignores them.
    This protects supported update/rollback paths; an OS owner can still replace files manually.
    """
    if any((st.root/name).exists() for name in ('credential_revocations.json','security_rotation.json')):
        from .update import compare
        if compare(version, '0.25.5a1') not in (0,1):
            raise ValueError('revocation_downgrade_blocked')
