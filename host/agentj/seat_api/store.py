"""Durable caller scopes, quotas and idempotency in the existing private bot DB.

No raw caller keys or provisioning capabilities are persisted: each endpoint has its own pepper, so a seat migration
moves verification material without moving the host-wide pepper. API turns share the bot engine's host budget and
concurrency. Unknown model usage keeps its reservation; restart never replays a model call.
"""
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
from ..bots.store import BotError, canonical, ident, slug, config as bot_config
from ..provider_profiles import atomic, secure_read
from . import e2e

DEFAULTS={'minute':10,'day':100,'tokens':100000}
CONCURRENCY=4;QUEUE=20;QUEUE_SECONDS=30
USAGE_DAYS=7;META_DAYS=30;RESULT_SECONDS=86400
RETRY=[60,300,1800,7200,21600]
EVENT_FIELDS=('event_id','account_id','seat_id','endpoint_uuid','key_id','request_id','job_id','contract_version','status','started_at','finished_at',
              'call_count','input_tokens','output_tokens','review_tokens','total_tokens','duration_ms','usage_source','provisional','sequence')
COLUMNS={'api_endpoints':[('epoch','INTEGER NOT NULL DEFAULT 1'),('workflow','TEXT'),('contract',"TEXT NOT NULL DEFAULT '{}'"),('public_spec','INTEGER NOT NULL DEFAULT 0'),('state',"TEXT NOT NULL DEFAULT 'active'")],
         'api_keys':[('issuer','TEXT')],'api_requests':[('started','INTEGER'),('callback','TEXT'),('authority','TEXT')],
         'api_deliveries':[('authority','TEXT')]}
class Store:
    def __init__(self,bots,profile_metadata=None,workflow_root=None,seat_id=''):
        self.bots=bots;self.db=bots.db;self.profile_metadata=profile_metadata;self.seat_id=seat_id
        self.workflow_root=workflow_root if workflow_root is not None else bots.root/'workflows'
        self.db.execute('PRAGMA secure_delete=ON')
        p=bots.root/'api-pepper'
        if not p.exists():atomic(p,secrets.token_bytes(32))
        self.pepper=secure_read(p)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS api_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL);
        INSERT OR IGNORE INTO api_state VALUES(1,0);
        CREATE TABLE IF NOT EXISTS api_endpoints(id TEXT PRIMARY KEY, public_id TEXT UNIQUE NOT NULL, bot TEXT NOT NULL, version INTEGER NOT NULL, privacy TEXT NOT NULL, enabled INTEGER NOT NULL, snapshot TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_keys(id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL, label TEXT NOT NULL, expires INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, limits TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_requests(id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, key_id TEXT NOT NULL, idem TEXT NOT NULL, digest TEXT NOT NULL, method TEXT NOT NULL, version INTEGER NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, status TEXT NOT NULL, response TEXT, UNIQUE(key_id,endpoint,method,version,idem));
        CREATE TABLE IF NOT EXISTS api_sessions(endpoint TEXT, key_id TEXT, conversation TEXT, visitor TEXT, capability TEXT, PRIMARY KEY(endpoint,key_id,conversation));
        CREATE TABLE IF NOT EXISTS api_endpoint_secrets(endpoint TEXT PRIMARY KEY, pepper BLOB NOT NULL, sign BLOB NOT NULL, kem BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS api_e2e_seen(key_id TEXT, request_id TEXT, expires INTEGER NOT NULL, PRIMARY KEY(key_id,request_id));
        CREATE TABLE IF NOT EXISTS api_usage(seq INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, event TEXT NOT NULL, sig TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS api_provisioners(id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, digest TEXT NOT NULL, label TEXT NOT NULL, max_keys INTEGER NOT NULL, max_days INTEGER NOT NULL, expires INTEGER NOT NULL, usage INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS api_provision_events(provisioner TEXT, event_id TEXT, key_id TEXT NOT NULL, created INTEGER NOT NULL, PRIMARY KEY(provisioner,event_id));
        CREATE TABLE IF NOT EXISTS api_callbacks(id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL, secret BLOB NOT NULL, recipient TEXT, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS api_deliveries(id TEXT PRIMARY KEY, callback TEXT NOT NULL, endpoint TEXT NOT NULL, key_id TEXT, event_id TEXT NOT NULL, body TEXT, attempts INTEGER NOT NULL DEFAULT 0, next INTEGER NOT NULL, created INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(callback,event_id));
        CREATE TABLE IF NOT EXISTS api_imports(id TEXT PRIMARY KEY, kem BLOB NOT NULL, expires INTEGER NOT NULL);
        ''')
        for table,cols in COLUMNS.items():
            have={r[1] for r in self.db.execute('PRAGMA table_info('+table+')')}
            for name,decl in cols:
                if name not in have:self.db.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+decl)
    def now(self):return self.bots.now()
    def hash(self,value):return hmac.new(self.pepper,value.encode(),hashlib.sha256).hexdigest()
    def secret(self,eid):
        row=self.db.execute('SELECT * FROM api_endpoint_secrets WHERE endpoint=?',(eid,)).fetchone()
        if not row:raise BotError('unknown_endpoint')
        return {'pepper':bytes(row['pepper']),'sign':bytes(row['sign']),'kem':bytes(row['kem'])}
    def ehash(self,eid,value):return hmac.new(self.secret(eid)['pepper'],value.encode(),hashlib.sha256).hexdigest()
    def enabled(self):return bool(self.db.execute('SELECT enabled FROM api_state').fetchone()[0])
    def switch(self,enabled):
        if type(enabled) is not bool:raise BotError('invalid_config')
        self.db.execute('UPDATE api_state SET enabled=?',(int(enabled),))
    def endpoint(self,eid):
        row=self.db.execute('SELECT * FROM api_endpoints WHERE id=?',(ident(eid),)).fetchone()
        if not row:raise BotError('unknown_endpoint')
        return dict(row)
    # ---------------------------------------------------------------- contract
    def components(self,bid,workflow=None,resolved_profile=None):
        """Labelled hashes of every contract input; the owner reviews which labels changed, never file content."""
        # Availability (enabled) is not contract: pausing/resuming or migrating a bot never widens or changes it.
        config=self.bots.get(bid);parts={'bot':hashlib.sha256(canonical({k:v for k,v in config.items() if k!='enabled'}).encode()).hexdigest()}
        if config['provider']['source']=='profile':
            try:
                if resolved_profile is not None:meta=resolved_profile
                elif self.profile_metadata is not None:meta=self.profile_metadata(config['provider']['id'])
                else:
                    from .. import provider_profiles
                    meta=next(p for p in provider_profiles.listing() if p['id']==config['provider']['id'])
                # Use the existing metadata-only profile reader; never read key
                # material for a contract hash. Credential rotation is separate.
                fields={k:meta[k] for k in ('base_url','model','api')}
                if any(not isinstance(v,str) or not v for v in fields.values()):raise ValueError()
                fields['base_url']=fields['base_url'].rstrip('/')
                fields['api']='anthropic' if fields['api']=='anthropic' else 'responses'
                parts['provider']=canonical(fields)
            except (OSError,ValueError,TypeError,KeyError,StopIteration):raise BotError('contract_unavailable') from None
        root=self.bots.directory(bid)
        for directory in ('knowledge','tools'):
            for p in sorted((root/directory).rglob('*')):
                if p.is_symlink():raise BotError('unsafe_contract')
                if p.is_file():parts[str(p.relative_to(root))]=hashlib.sha256(secure_read(p)).hexdigest()
        if workflow:
            from . import workflow as wf
            parts['workflow']=wf.digest(self.workflow_root,workflow)
        return parts
    def snapshot(self,bid,resolved_profile=None,workflow=None):
        return hashlib.sha256(canonical(self.components(bid,workflow,resolved_profile)).encode()).hexdigest()
    def create(self,name,bid,privacy='e2e',rest_accepted=False,workflow=None):
        name=slug(name);self.bots.get(bid)
        if name in ('hook','_e2e','_provision','_report','chat','jobs'):raise BotError('invalid_name')
        if privacy not in ('e2e','rest'):raise BotError('invalid_privacy')
        if privacy=='rest' and rest_accepted is not True:raise BotError('rest_disclosure_required')
        if workflow is not None:
            from . import workflow as wf
            workflow=slug(workflow);wf.load(self.workflow_root,workflow)
        # REST only publishes the already-restricted bot or sandboxed workflow. No main-session type.
        eid=secrets.token_hex(16);parts=self.components(bid,workflow);snapshot=self.snapshot(bid,workflow=workflow)
        try:
            with self.bots.transaction():
                self.db.execute('INSERT INTO api_endpoints(id,public_id,bot,version,privacy,enabled,snapshot,epoch,workflow,contract) VALUES(?,?,?,1,?,0,?,1,?,?)',(eid,name,bid,privacy,snapshot,workflow,canonical(parts)))
                keys=e2e.new_keys();self.db.execute('INSERT INTO api_endpoint_secrets VALUES(?,?,?,?)',(eid,secrets.token_bytes(32),keys['sign'],keys['kem']))
        except sqlite3.IntegrityError:raise BotError('name_taken') from None
        return self.endpoint(eid)
    def current(self,e):return self.snapshot(e['bot'],workflow=e['workflow'])
    def review(self,eid):
        """Owner review of a pending contract change: changed labels only, plus the hash to sign."""
        e=self.endpoint(eid);new=self.components(e['bot'],e['workflow']);old=json.loads(e['contract'] or '{}')
        return {'version':e['version'],'current':e['snapshot'],'pending':self.current(e),
                'changed':sorted(k for k in set(old)|set(new) if old.get(k)!=new.get(k))}
    def publish_version(self,eid,reviewed):
        """The owner signed the exact pending hash. Old keys stay on the old version: no silent widening."""
        e=self.endpoint(eid);pending=self.current(e)
        if not isinstance(reviewed,str) or reviewed!=pending:raise BotError('stale_review')
        if pending==e['snapshot']:return e
        self.db.execute('UPDATE api_endpoints SET version=version+1,snapshot=?,contract=? WHERE id=?',(pending,canonical(self.components(e['bot'],e['workflow'])),eid))
        return self.endpoint(eid)
    def rebind(self,kid):
        row=self.db.execute('SELECT * FROM api_keys WHERE id=?',(ident(kid),)).fetchone()
        if not row or row['revoked']:raise BotError('unknown_key')
        e=self.endpoint(row['endpoint']);self.db.execute('UPDATE api_keys SET version=? WHERE id=?',(e['version'],kid));return e['version']
    def require_qualified(self,e):
        if e['state']!='active':raise BotError('endpoint_'+e['state'] if e['state'] in ('migrating','migrated','taken_down') else 'offline')
        # Native CLI output ceilings have not passed ADR-A193 qualification (no upstream hard
        # output cap). Do not publish or admit them through the API.
        provider=self.bots.get(e['bot'])['provider']
        if provider.get('chain'):raise BotError('native_unqualified')  # no unpinned/native fallback executor
        if provider['source']!='profile':
            raise BotError('native_unqualified')
        if e['workflow']:
            from . import workflow as wf
            if not wf.qualify()['supported']:raise BotError('workflow_unsupported')
        if e['snapshot']!=self.current(e):raise BotError('contract_changed')
    def publish(self,eid,enabled):
        if type(enabled) is not bool:raise BotError('invalid_config')
        e=self.endpoint(eid)
        if enabled:
            if not self.enabled():raise BotError('api_disabled')
            if not self.bots.get(e['bot'])['enabled']:raise BotError('bot_offline')
            self.require_qualified(e)
        self.db.execute('UPDATE api_endpoints SET enabled=? WHERE id=?',(int(enabled),eid))
    def publish_spec(self,eid,public):
        if type(public) is not bool:raise BotError('invalid_config')
        self.endpoint(eid);self.db.execute('UPDATE api_endpoints SET public_spec=? WHERE id=?',(int(public),eid))
    # ---------------------------------------------------------------- keys
    def _label(self,label):
        if not isinstance(label,str) or not 1<=len(label)<=80 or any(ord(x)<32 for x in label):raise BotError('invalid_label')
        from .. import privacy
        if any(p.search(label) for _,p,_ in privacy._SECRETS):raise BotError('invalid_label')
        return label
    def issue(self,eid,label,days=30,limits=None,issuer=None):
        e=self.endpoint(eid)
        if not self.enabled() or not e['enabled']:raise BotError('api_disabled')
        self.require_qualified(e)
        if type(days) is not int or not 1<=days<=30:raise BotError('invalid_expiry')
        label=self._label(label)
        limits={**DEFAULTS,**(limits or {})}
        if set(limits)!=set(DEFAULTS) or any(type(v) is not int or not 1<=v<=DEFAULTS[k] for k,v in limits.items()):raise BotError('invalid_limits')
        kid=secrets.token_hex(16);raw='ajapi_'+eid+'_'+kid+'_'+secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO api_keys(id,endpoint,version,digest,label,expires,revoked,limits,issuer) VALUES(?,?,?,?,?,?,0,?,?)',(kid,eid,e['version'],self.ehash(eid,raw),label,self.now()+days*86400,canonical(limits),issuer))
        return {'id':kid,'secret':raw,'expires_at':self.now()+days*86400}
    def revoke(self,kid):
        ident(kid)
        with self.bots.transaction():
            self.db.execute('UPDATE api_keys SET revoked=1 WHERE id=?',(kid,))
            self.db.execute("UPDATE api_requests SET status='cancelled',response=NULL WHERE key_id=? AND status='queued'",(kid,))
            self.db.execute("UPDATE api_deliveries SET status='cancelled',body=NULL WHERE key_id=? AND status='pending'",(kid,))
    def authenticate(self,eid,raw):
        match=re.fullmatch(r'ajapi_([a-f0-9]{32})_([a-f0-9]{32})_[A-Za-z0-9_-]{43}',raw) if isinstance(raw,str) else None
        if not match:raise BotError('key_invalid')
        row=self.db.execute('SELECT * FROM api_keys WHERE id=?',(match.group(2),)).fetchone()
        if not row or match.group(1)!=row['endpoint']:raise BotError('key_invalid')
        try:digest=self.ehash(row['endpoint'],raw)
        except BotError:raise BotError('key_invalid') from None
        if not hmac.compare_digest(row['digest'],digest) or row['revoked'] or row['expires']<=self.now():raise BotError('key_invalid')
        e=self.endpoint(eid)
        if match.group(1)!=eid or row['endpoint']!=eid or row['version']!=e['version']:raise BotError('key_scope')
        if not self.enabled() or not e['enabled']:raise BotError('api_disabled')
        self.require_qualified(e)
        return dict(row)
    # ---------------------------------------------------------------- E2E replay window
    def seen(self,key_id,request_id,deadline):
        now=self.now()
        with self.bots.transaction():
            self.db.execute('DELETE FROM api_e2e_seen WHERE expires<=?',(now,))
            try:self.db.execute('INSERT INTO api_e2e_seen VALUES(?,?,?)',(key_id,request_id,deadline+60))
            except sqlite3.IntegrityError:raise BotError('replay') from None
    # ---------------------------------------------------------------- admission, queue, results
    def admit(self,eid,key,method,idem,body,callback=None):
        if not isinstance(idem,str) or not re.fullmatch(r'[\x21-\x7e]{1,64}',idem):raise BotError('idempotency_required')
        idem=self.ehash(eid,'idempotency:'+idem)
        now=self.now();digest=self.ehash(eid,canonical(body));rid=secrets.token_hex(16)
        with self.bots.transaction():
            old=self.db.execute('SELECT * FROM api_requests WHERE key_id=? AND endpoint=? AND method=? AND version=? AND idem=?',(key['id'],eid,method,key['version'],idem)).fetchone()
            if old:
                if old['digest']!=digest:raise BotError('idempotency_conflict')
                # Keep a tombstone after retention. Never turn a timed-out side effect into a new call.
                if old['expires']<=now:raise BotError('request_expired')
                return dict(old),False
            limits=json.loads(key['limits'])
            for cutoff,maximum in [(now-60,limits['minute']),(now//86400*86400,limits['day'])]:
                if self.db.execute('SELECT count(*) FROM api_requests WHERE key_id=? AND created>=?',(key['id'],cutoff)).fetchone()[0]>=maximum:raise BotError('rate_limit')
            if self.db.execute('SELECT count(*) FROM api_requests WHERE endpoint=? AND created>?',(eid,now-60)).fetchone()[0]>=30:raise BotError('rate_limit')
            # Seat-wide: at most 20 waiting for one of the 4 shared bot/API execution slots.
            if self.db.execute("SELECT count(*) FROM api_requests WHERE status='queued'").fetchone()[0]>=QUEUE:raise BotError('queue_full')
            e=self.endpoint(eid)
            used=self.db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM calls WHERE bot=? AND visitor IN (SELECT visitor FROM api_sessions WHERE key_id=?) AND day=?',(e['bot'],key['id'],self.bots.day())).fetchone()[0]
            if used>=limits['tokens']:raise BotError('token_limit')
            self.db.execute('INSERT INTO api_requests(id,endpoint,key_id,idem,digest,method,version,created,expires,status,response,callback) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?)',(rid,eid,key['id'],idem,digest,method,key['version'],now,now+RESULT_SECONDS,'queued',callback))
            return dict(self.db.execute('SELECT * FROM api_requests WHERE id=?',(rid,)).fetchone()),True
    def try_start(self,rid):
        """Claim one of the seat's shared execution slots (bot turns + API) for a queued request."""
        with self.bots.transaction():
            running=self.db.execute("SELECT count(*) FROM api_requests WHERE status='running'").fetchone()[0]+self.db.execute("SELECT count(*) FROM turns WHERE status='running'").fetchone()[0]
            if running>=CONCURRENCY:return False
            return self.db.execute("UPDATE api_requests SET status='running',started=? WHERE id=? AND status='queued'",(self.now(),rid)).rowcount==1
    def status(self,rid):
        row=self.db.execute('SELECT status FROM api_requests WHERE id=?',(rid,)).fetchone();return row['status'] if row else None
    def session(self,eid,key,conversation):
        if not isinstance(conversation,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',conversation):raise BotError('invalid_conversation')
        e=self.endpoint(eid)
        row=self.db.execute('SELECT visitor,capability FROM api_sessions WHERE endpoint=? AND key_id=? AND conversation=?',(eid,key['id'],conversation)).fetchone()
        if row:return tuple(row)
        vid,cap=self.bots.visitor(e['bot'])
        self.db.execute('INSERT INTO api_sessions VALUES(?,?,?,?,?)',(eid,key['id'],conversation,vid,cap));return vid,cap
    def finish(self,rid,result,status):
        # Wall time can pass retention while a machine sleeps. A late engine
        # completion must not resurrect the API result or its bot cache copies.
        with self.bots.transaction():
            row=self.db.execute('SELECT expires,status FROM api_requests WHERE id=?',(rid,)).fetchone()
            if not row or row['expires']<=self.now() or row['status'] not in ('running','queued'):
                if not row or row['expires']<=self.now() or row['status'] in ('deleted','cancelled','failed','expired'):
                    self.db.execute('DELETE FROM messages WHERE request=? AND visitor IN (SELECT visitor FROM api_sessions)',(rid,))
                    self.db.execute('UPDATE turns SET answer=NULL WHERE request=? AND visitor IN (SELECT visitor FROM api_sessions)',(rid,))
                    if row and row['expires']<=self.now():
                        self.db.execute("UPDATE api_requests SET response=NULL,status='expired' WHERE id=?",(rid,))
                return False
            done=self.db.execute("UPDATE api_requests SET response=?,status=? WHERE id=? AND status IN ('running','queued')",(canonical(result),status,rid)).rowcount==1
            if done:self.meter(rid,status)
            return done
    def result(self,eid,key,rid):
        row=self.db.execute('SELECT * FROM api_requests WHERE id=? AND endpoint=? AND key_id=?',(ident(rid),eid,key['id'])).fetchone()
        if not row:raise BotError('unknown_request')
        if row['expires']<=self.now():raise BotError('request_expired')
        return dict(row)
    def recover(self):
        self.db.execute("UPDATE api_requests SET status='failed',response=NULL WHERE status IN ('queued','running')")
        self.db.execute("UPDATE api_endpoints SET state='active' WHERE state='migrating'")
    def erase(self,rid):
        rid=ident(rid)
        with self.bots.transaction():
            self.db.execute("UPDATE api_requests SET status='deleted',response=NULL WHERE id=?",(rid,))
            self.db.execute('DELETE FROM messages WHERE request=? AND visitor IN (SELECT visitor FROM api_sessions)',(rid,))
            self.db.execute('UPDATE turns SET answer=NULL WHERE request=? AND visitor IN (SELECT visitor FROM api_sessions)',(rid,))
            self.db.execute("UPDATE api_deliveries SET status='cancelled',body=NULL WHERE event_id=? AND status='pending'",(rid,))
    def prune(self):
        # API bodies also live in the reused bot conversation/cache; remove every API copy at 24h.
        # Retain numeric call reservations and request tombstones for budgets/idempotency.
        now=self.now()
        with self.bots.transaction():
            self.db.execute('DELETE FROM messages WHERE request IN (SELECT id FROM api_requests WHERE expires<=?) AND visitor IN (SELECT visitor FROM api_sessions)',(now,))
            self.db.execute('UPDATE turns SET answer=NULL WHERE request IN (SELECT id FROM api_requests WHERE expires<=?) AND visitor IN (SELECT visitor FROM api_sessions)',(now,))
            self.db.execute('UPDATE api_requests SET response=NULL WHERE expires<=?',(now,))
            self.db.execute('DELETE FROM api_requests WHERE created<?',(now-META_DAYS*86400,))
            self.db.execute('DELETE FROM api_usage WHERE created<?',(now-USAGE_DAYS*86400,))
            self.db.execute("UPDATE api_deliveries SET status='expired',body=NULL WHERE status='pending' AND created<=?",(now-86400,))
            self.db.execute('DELETE FROM api_deliveries WHERE created<?',(now-META_DAYS*86400,))
            self.db.execute('DELETE FROM api_e2e_seen WHERE expires<=?',(now,))
            self.db.execute('DELETE FROM api_imports WHERE expires<=?',(now,))
    def metadata(self):
        return {'enabled':self.enabled(),'endpoints':[{**dict(x),**(self.public_keys(x['id']) if self.db.execute('SELECT 1 FROM api_endpoint_secrets WHERE endpoint=?',(x['id'],)).fetchone() else {'pin':None,'sign_pk':None})} for x in self.db.execute('SELECT id,public_id,bot,version,privacy,enabled,snapshot,epoch,workflow,public_spec,state FROM api_endpoints')],
                'keys':[dict(x) for x in self.db.execute('SELECT id,endpoint,version,label,expires,revoked,limits,issuer FROM api_keys')],
                'provisioners':[dict(x) for x in self.db.execute('SELECT id,endpoint,label,max_keys,max_days,expires,usage,revoked FROM api_provisioners')],
                'callbacks':[dict(x) for x in self.db.execute('SELECT id,endpoint,kind,url,enabled,recipient IS NOT NULL sealed FROM api_callbacks')]}
    def public_keys(self,eid):
        pub=e2e.public(self.secret(eid));return {'pin':e2e.pin(eid,pub['sign'],pub['kem']),'sign_pk':pub['sign']}
    # ---------------------------------------------------------------- metering (signed outbox, 7 days)
    def meter(self,rid,status):
        r=self.db.execute('SELECT * FROM api_requests WHERE id=?',(rid,)).fetchone()
        u=self.db.execute("SELECT count(*) n,sum(CASE WHEN kind!='jev' THEN actual END) model,sum(CASE WHEN kind='jev' THEN actual END) review,sum(actual IS NULL) unknown FROM calls WHERE request=?",(rid,)).fetchone()
        known=not u['unknown']
        # AUTOINCREMENT high-water mark: pruned (7-day) events never let a sequence number be reused.
        seq=max(self.db.execute('SELECT coalesce(max(seq),0) FROM api_usage').fetchone()[0],(self.db.execute("SELECT coalesce(max(seq),0) FROM sqlite_sequence WHERE name='api_usage'").fetchone() or [0])[0])+1
        event={'event_id':hashlib.sha256(('usage:'+rid).encode()).hexdigest()[:32],'account_id':None,'seat_id':self.seat_id or None,'endpoint_uuid':r['endpoint'],
               'key_id':r['key_id'],'request_id':rid,'job_id':rid if r['method']=='POST:jobs' else None,'contract_version':r['version'],'status':status,
               'started_at':r['started'],'finished_at':self.now(),'call_count':1,'input_tokens':None,'output_tokens':None,
               'review_tokens':u['review'] if known and u['n'] else (0 if not u['n'] else None),
               'total_tokens':((u['model'] or 0)+(u['review'] or 0)) if known else None,
               'duration_ms':(self.now()-r['started'])*1000 if r['started'] else None,'usage_source':'provider' if known else 'unknown',
               'provisional':not known,'sequence':seq}
        sig=e2e.sign(self.secret(r['endpoint']),'agentjarvis/seat-api-usage-v1',event)
        self.db.execute('INSERT INTO api_usage(seq,endpoint,event,sig,created) VALUES(?,?,?,?,?)',(seq,r['endpoint'],canonical(event),sig,self.now()))
        for cb in self.db.execute("SELECT id,endpoint FROM api_callbacks WHERE endpoint=? AND kind='usage' AND enabled=1",(r['endpoint'],)).fetchall():
            self.enqueue(cb['id'],event['event_id'],{'type':'usage','event':event,'sig':sig},None,authority=r['authority'])
        if r['callback'] and status in ('succeeded','failed','cancelled'):
            cb=self.db.execute("SELECT id FROM api_callbacks WHERE id=? AND endpoint=? AND kind='result' AND enabled=1",(r['callback'],r['endpoint'])).fetchone()
            if cb:self.enqueue(cb['id'],rid,{'type':'result','job_id':rid,'status':status},r['key_id'],authority=r['authority'])
    def usage(self,eid=None,since=0,limit=1000,fmt='json'):
        if type(since) is not int or since<0 or type(limit) is not int or not 1<=limit<=5000:raise BotError('invalid_request')
        rows=self.db.execute('SELECT seq,event,sig FROM api_usage WHERE seq>?'+(' AND endpoint=?' if eid else '')+' ORDER BY seq LIMIT ?',(since,eid,limit) if eid else (since,limit)).fetchall()
        events=[{'seq':r['seq'],'event':json.loads(r['event']),'sig':r['sig']} for r in rows]
        first=self.db.execute('SELECT min(seq) FROM api_usage').fetchone()[0]
        # Sequence gaps (pruned beyond 7 days or never delivered) are reported, not hidden.
        out={'events':events,'next':events[-1]['seq'] if events else since,'pruned_before':first,'gap':bool(first and since+1<first)}
        if fmt=='csv':
            buf=io.StringIO();w=csv.writer(buf);w.writerow(EVENT_FIELDS+('sig',))
            for e in events:w.writerow([('' if e['event'][k] is None else e['event'][k]) for k in EVENT_FIELDS]+[e['sig']])
            out={'csv':buf.getvalue(),'next':out['next'],'gap':out['gap']}
        elif fmt!='json':raise BotError('invalid_request')
        return out
    # ---------------------------------------------------------------- provisioning capability
    def provisioner(self,eid,label,max_keys=10,max_days=30,days=30,usage=False):
        e=self.endpoint(eid)
        if not self.enabled() or not e['enabled']:raise BotError('api_disabled')
        if type(max_keys) is not int or not 1<=max_keys<=1000 or type(max_days) is not int or not 1<=max_days<=30 or type(days) is not int or not 1<=days<=90 or type(usage) is not bool:raise BotError('invalid_limits')
        pid=secrets.token_hex(16);raw='ajprov_'+eid+'_'+pid+'_'+secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO api_provisioners VALUES(?,?,?,?,?,?,?,?,0)',(pid,eid,self.ehash(eid,raw),self._label(label),max_keys,max_days,self.now()+days*86400,int(usage)))
        return {'id':pid,'secret':raw,'expires_at':self.now()+days*86400}
    def revoke_provisioner(self,pid):
        self.db.execute('UPDATE api_provisioners SET revoked=1 WHERE id=?',(ident(pid),))
    def provision(self,eid,key_id,inner):
        """Only the capability's own endpoint, its own keys, within its own totals; never publish/permissions."""
        raw=inner.get('cap') if isinstance(inner,dict) else None
        m=re.fullmatch(r'ajprov_([a-f0-9]{32})_([a-f0-9]{32})_[A-Za-z0-9_-]{43}',raw) if isinstance(raw,str) else None
        row=self.db.execute('SELECT * FROM api_provisioners WHERE id=?',(m.group(2),)).fetchone() if m else None
        if not row or m.group(1)!=eid or row['endpoint']!=eid or m.group(2)!=key_id or not hmac.compare_digest(row['digest'],self.ehash(eid,raw)) or row['revoked'] or row['expires']<=self.now():raise BotError('key_invalid')
        op=inner.get('op');pid=row['id']
        if op=='issue':
            if set(inner)-{'cap','op','event_id','label','days'}:raise BotError('invalid_input')
            event=inner.get('event_id')
            if not isinstance(event,str) or not re.fullmatch(r'[\x21-\x7e]{1,64}',event):raise BotError('idempotency_required')
            ev=self.ehash(eid,'provision:'+event)
            old=self.db.execute('SELECT key_id FROM api_provision_events WHERE provisioner=? AND event_id=?',(pid,ev)).fetchone()
            if old:return {'key_id':old['key_id'],'replay':True}
            days=inner.get('days',row['max_days'])
            if type(days) is not int or not 1<=days<=row['max_days']:raise BotError('invalid_expiry')
            if self.db.execute('SELECT count(*) FROM api_keys WHERE issuer=?',(pid,)).fetchone()[0]>=row['max_keys']:raise BotError('provision_limit')
            issued=self.issue(eid,inner.get('label') or 'provisioned',days,None,issuer=pid)
            self.db.execute('INSERT INTO api_provision_events VALUES(?,?,?,?)',(pid,ev,issued['id'],self.now()))
            return {'key_id':issued['id'],'secret':issued['secret'],'expires_at':issued['expires_at']}
        if op=='revoke':
            kid=inner.get('key_id')
            if not isinstance(kid,str) or not self.db.execute('SELECT 1 FROM api_keys WHERE id=? AND issuer=?',(kid,pid)).fetchone():raise BotError('unknown_key')
            self.revoke(kid);return {'revoked':True}
        if op=='list':return {'keys':[dict(x) for x in self.db.execute('SELECT id,label,expires,revoked FROM api_keys WHERE issuer=?',(pid,))]}
        if op=='usage':
            if not row['usage']:raise BotError('key_scope')
            since=inner.get('since',0);return self.usage(eid,since if type(since) is int else -1)
        raise BotError('unsupported')
    # ---------------------------------------------------------------- callbacks
    def save_callback(self,eid,kind,url,recipient=None):
        from .callbacks import check_url
        e=self.endpoint(eid)
        if kind not in ('result','usage'):raise BotError('invalid_callback')
        url=check_url(url)
        if e['privacy']=='e2e':
            from ..wire import unb64u
            try:
                if len(unb64u(recipient))!=32:raise ValueError()
            except (ValueError,TypeError):raise BotError('recipient_required') from None
        elif recipient is not None:raise BotError('invalid_callback')
        cid=secrets.token_hex(16);secret=secrets.token_bytes(32)
        self.db.execute('INSERT INTO api_callbacks VALUES(?,?,?,?,?,?,1)',(cid,eid,kind,url,secret,recipient))
        return {'id':cid,'secret':'ajwhsec_'+secret.hex()}
    def delete_callback(self,cid):
        with self.bots.transaction():
            self.db.execute('DELETE FROM api_callbacks WHERE id=?',(ident(cid),))
            self.db.execute("UPDATE api_deliveries SET status='cancelled',body=NULL WHERE callback=? AND status='pending'",(cid,))
    def enqueue(self,cid,event_id,body,key_id,authority=None):
        cb=self.db.execute('SELECT * FROM api_callbacks WHERE id=?',(cid,)).fetchone()
        if cb['recipient']:body=e2e.seal_to(cb['recipient'],'agentjarvis/seat-api-callback-v1',body)
        self.db.execute('INSERT OR IGNORE INTO api_deliveries(id,callback,endpoint,key_id,event_id,body,attempts,next,created,status) VALUES(?,?,?,?,?,?,0,?,?,?)',
                        (secrets.token_hex(16),cid,cb['endpoint'],key_id,event_id,canonical(body),self.now(),self.now(),'pending'))
        if authority is not None:self.db.execute('UPDATE api_deliveries SET authority=? WHERE callback=? AND event_id=?',(authority,cid,event_id))
    def due(self,limit=10):
        return [dict(r) for r in self.db.execute("SELECT d.*,c.url,c.secret FROM api_deliveries d JOIN api_callbacks c ON c.id=d.callback WHERE d.status='pending' AND d.next<=? AND c.enabled=1 ORDER BY d.next LIMIT ?",(self.now(),limit))]
    def delivered(self,did,ok):
        row=self.db.execute('SELECT attempts,created FROM api_deliveries WHERE id=?',(did,)).fetchone()
        if ok:self.db.execute("UPDATE api_deliveries SET status='delivered',body=NULL,attempts=attempts+1 WHERE id=?",(did,));return
        n=row['attempts']+1;nxt=self.now()+RETRY[n-1] if n<=len(RETRY) else None
        if nxt is None or nxt>row['created']+86400:self.db.execute("UPDATE api_deliveries SET status='failed',body=NULL,attempts=? WHERE id=?",(n,did))
        else:self.db.execute('UPDATE api_deliveries SET attempts=?,next=? WHERE id=?',(n,nxt,did))
    # ---------------------------------------------------------------- seat migration (same account)
    def import_key(self):
        iid=secrets.token_hex(16);private,pub=e2e.x25519_pair()
        self.db.execute('INSERT INTO api_imports VALUES(?,?,?)',(iid,private,self.now()+600));return {'import_id':iid,'import_pk':pub}
    def begin_migration(self,eid):
        e=self.endpoint(eid)
        if e['state']!='active':raise BotError('endpoint_'+e['state'])
        self.db.execute("UPDATE api_endpoints SET state='migrating' WHERE id=?",(eid,))
    def export(self,eid,import_pk,host_sk):
        """Encrypted to the target's one-time import key, signed by this host. No raw keys, no bodies."""
        e=self.endpoint(eid)
        if e['state']!='migrating':raise BotError('migration_not_started')
        if self.db.execute("SELECT count(*) FROM api_requests WHERE endpoint=? AND status IN ('queued','running')",(eid,)).fetchone()[0]:raise BotError('jobs_pending')
        root=self.bots.directory(e['bot']);files={}
        for directory in ('knowledge','tools'):
            for p in sorted((root/directory).rglob('*')):
                if p.is_symlink():raise BotError('unsafe_contract')
                if p.is_file():files[str(p.relative_to(root))]=__import__('base64').b64encode(secure_read(p)).decode()
        if sum(len(v) for v in files.values())>8*1024*1024:raise BotError('too_large')
        s=self.secret(eid)
        payload={'v':1,'endpoint':{k:e[k] for k in ('id','public_id','version','privacy','snapshot','epoch','workflow','contract','public_spec')},
                 'bot':{'id':e['bot'],'config':self.bots.get(e['bot']),'files':files},
                 'secrets':{k:s[k].hex() for k in ('pepper','sign','kem')},
                 'keys':[dict(r) for r in self.db.execute('SELECT * FROM api_keys WHERE endpoint=?',(eid,))],
                 'provisioners':[dict(r) for r in self.db.execute('SELECT * FROM api_provisioners WHERE endpoint=?',(eid,))],
                 'requests':[{**dict(r),'response':None} for r in self.db.execute('SELECT * FROM api_requests WHERE endpoint=?',(eid,))]}
        if e['workflow']:raise BotError('workflow_migration_unsupported')
        box=e2e.seal_to(import_pk,'agentjarvis/seat-api-migration-v1',payload)
        from ..wire import b64u
        sig=b64u(host_sk.sign(('agentjarvis/seat-api-migration-v1\n'+canonical(box)).encode()))
        return {'box':box,'sig':sig,'from_epoch':e['epoch']}
    def finish_export(self,eid):
        # The old host never serves again; its keys and E2E keys are erased here.
        with self.bots.transaction():
            self.db.execute("UPDATE api_endpoints SET state='migrated',enabled=0 WHERE id=?",(eid,))
            self.db.execute('DELETE FROM api_endpoint_secrets WHERE endpoint=?',(eid,))
            self.db.execute('UPDATE api_keys SET revoked=1 WHERE endpoint=?',(eid,))
            self.db.execute('UPDATE api_provisioners SET revoked=1 WHERE endpoint=?',(eid,))
    def abort_migration(self,eid):
        e=self.endpoint(eid)
        if e['state']!='migrating':raise BotError('migration_not_started')
        self.db.execute("UPDATE api_endpoints SET state='active' WHERE id=?",(eid,))
    def import_bundle(self,import_id,bundle,source_pk):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from ..wire import unb64u
        row=self.db.execute('SELECT * FROM api_imports WHERE id=? AND expires>?',(ident(import_id),self.now())).fetchone()
        if not row or not isinstance(bundle,dict) or set(bundle)!={'box','sig','from_epoch'}:raise BotError('invalid_bundle')
        try:Ed25519PublicKey.from_public_bytes(unb64u(source_pk)).verify(unb64u(bundle['sig']),('agentjarvis/seat-api-migration-v1\n'+canonical(bundle['box'])).encode())
        except (InvalidSignature,ValueError,TypeError):raise BotError('invalid_bundle') from None
        p=e2e.open_sealed(bytes(row['kem']),'agentjarvis/seat-api-migration-v1',bundle['box'])
        e=p['endpoint']
        if p.get('v')!=1 or e['epoch']!=bundle['from_epoch']:raise BotError('invalid_bundle')
        import base64
        with self.bots.transaction():
            self.db.execute('DELETE FROM api_imports WHERE id=?',(import_id,))
            if self.db.execute('SELECT 1 FROM api_endpoints WHERE id=? OR public_id=?',(e['id'],e['public_id'])).fetchone():raise BotError('name_taken')
            bid=p['bot']['id']
            if not self.db.execute('SELECT 1 FROM bots WHERE id=?',(ident(bid),)).fetchone():
                cfg=bot_config({**p['bot']['config'],'enabled':False})
                self.db.execute('INSERT INTO bots VALUES(?,?)',(bid,canonical(cfg)))
                self.db.execute('INSERT INTO bot_lifecycle VALUES(?,1,0)',(bid,))
        root=self.bots.directory(bid)
        for rel,data in p['bot']['files'].items():
            target=(root/rel).resolve()
            if not str(target).startswith(str(root.resolve())+'/') or rel.split('/')[0] not in ('knowledge','tools'):raise BotError('invalid_bundle')
            target.parent.mkdir(parents=True,exist_ok=True);atomic(target,base64.b64decode(data))
        with self.bots.transaction():
            self.db.execute('INSERT INTO api_endpoints(id,public_id,bot,version,privacy,enabled,snapshot,epoch,workflow,contract,public_spec,state) VALUES(?,?,?,?,?,0,?,?,?,?,?,?)',
                            (e['id'],e['public_id'],bid,e['version'],e['privacy'],e['snapshot'],e['epoch']+1,e['workflow'],e['contract'],e['public_spec'],'active'))
            s=p['secrets'];self.db.execute('INSERT INTO api_endpoint_secrets VALUES(?,?,?,?)',(e['id'],bytes.fromhex(s['pepper']),bytes.fromhex(s['sign']),bytes.fromhex(s['kem'])))
            for k in p['keys']:self.db.execute('INSERT INTO api_keys(id,endpoint,version,digest,label,expires,revoked,limits,issuer) VALUES(?,?,?,?,?,?,?,?,?)',tuple(k.get(c) for c in ('id','endpoint','version','digest','label','expires','revoked','limits','issuer')))
            for v in p['provisioners']:self.db.execute('INSERT INTO api_provisioners VALUES(?,?,?,?,?,?,?,?,?)',tuple(v[c] for c in ('id','endpoint','digest','label','max_keys','max_days','expires','usage','revoked')))
            # Idempotency tombstones only: a retried call after migration never runs twice or bills twice.
            for r in p['requests']:
                self.db.execute('INSERT OR IGNORE INTO api_requests(id,endpoint,key_id,idem,digest,method,version,created,expires,status,response,started,callback) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?)',
                                (r['id'],r['endpoint'],r['key_id'],r['idem'],r['digest'],r['method'],r['version'],r['created'],r['expires'],r['status'] if r['status'] not in ('queued','running') else 'failed',r.get('started'),r.get('callback')))
        return {'endpoint':self.endpoint(e['id']),'contract_matches':self.snapshot_ok(e['id'])}
    def snapshot_ok(self,eid):
        try:e=self.endpoint(eid);return e['snapshot']==self.current(e)
        except BotError:return False
