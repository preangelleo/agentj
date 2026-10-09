"""Private SQLite bot state and durable, transactionally reserved budgets.

Unsettled calls remain charged across restart. No message/secret is an activity field.
All bot IDs, visitor capabilities and tool call counters are host-local.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import unicodedata
from zoneinfo import ZoneInfo
from ..provider_profiles import atomic, private_dir, secure_read
from .. import privacy

class BotError(Exception):
    """Only fixed codes may leave this boundary, never upstream errors."""

ID = re.compile(r'[a-f0-9]{32}')
RESERVED = {'agentj','jarvis','admin','support','api','friends','bots','login','verify','www','root','help','billing','account','dashboard','terms','privacy','null','undefined'}
LIMITS = {'visitor_messages':20, 'bot_messages':100, 'host_messages':100, 'host_tokens':100000, 'concurrency':4}

def slug(value):
    if not isinstance(value,str): raise BotError('invalid_name')
    value = unicodedata.normalize('NFKC',value).lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,30}[a-z0-9]',value) or value in RESERVED:
        raise BotError('invalid_name')
    return value

def ident(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise BotError('invalid_id')
    return value

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def config(raw):
    if not isinstance(raw,dict):raise BotError('invalid_config')
    allowed={'slug','title','description','prompt','background','language','timezone','hours','enabled','terms_accepted','subscription_risk_accepted','limits','provider','outbound_domains','embed_origins'}
    if set(raw)-allowed:raise BotError('invalid_config')
    out={'slug':slug(raw.get('slug')), 'title':'Customer service / 客服','description':'AI customer service / AI 客服',
         'prompt':'Answer only questions within the owner-provided business knowledge. If unsure, offer a human.',
         'background':'', 'language':'auto','timezone':'UTC','hours':None,'enabled':False,'terms_accepted':False,'subscription_risk_accepted':False,
         'limits':dict(LIMITS),'provider':{'source':'main'},'outbound_domains':[],'embed_origins':[]}
    out.update(raw)
    for k,limit in [('title',80),('description',300),('prompt',6000),('background',6000)]:
        if not isinstance(out[k],str) or len(out[k])>limit or any(ord(c)<32 and c not in '\n\t' for c in out[k]):raise BotError('invalid_config')
    if any(pattern.search(out[k]) for k in ('prompt','background','description','title') for _,pattern,_ in privacy._SECRETS):raise BotError('config_contains_secret')
    if out['language'] not in ('auto','zh','en') or type(out['enabled']) is not bool or type(out['terms_accepted']) is not bool or type(out['subscription_risk_accepted']) is not bool:raise BotError('invalid_config')
    try:ZoneInfo(out['timezone'])
    except (ValueError,TypeError,KeyError):raise BotError('invalid_timezone') from None
    limits={**LIMITS,**out['limits']} if isinstance(out['limits'],dict) else {}
    if set(limits)!=set(LIMITS):raise BotError('invalid_limits')
    for k,v in limits.items():
        ceiling=16 if k=='concurrency' else LIMITS[k]
        if type(v) is not int or not 1<=v<=ceiling:raise BotError('invalid_limits')
    out['limits']=limits
    hours=out['hours']
    if hours is not None and (not isinstance(hours,dict) or set(hours)!={'start','end','days'} or
        any(type(hours.get(k)) is not int or not 0<=hours[k]<24 for k in ('start','end')) or
        not isinstance(hours.get('days'),list) or not hours['days'] or any(type(d) is not int or not 0<=d<7 for d in hours['days'])):raise BotError('invalid_hours')
    p=out['provider']
    if not isinstance(p,dict) or p.get('source') not in ('main','profile') or set(p)-{'source','id'} or p['source']=='profile' and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}',p.get('id','')):raise BotError('invalid_provider')
    domains=out['outbound_domains']
    if not isinstance(domains,list) or len(domains)>20 or any(not isinstance(x,str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',x) or '.' not in x for x in domains):raise BotError('invalid_domains')
    from urllib.parse import urlsplit
    origins=out['embed_origins']
    if not isinstance(origins,list) or len(origins)>20:raise BotError('invalid_origins')
    for origin in origins:
        try:u=urlsplit(origin)
        except (ValueError,TypeError):raise BotError('invalid_origins') from None
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:raise BotError('invalid_origins')
    if out['enabled'] and not out['terms_accepted']:raise BotError('accept_terms')
    return out

class Store:
    def __init__(self,root:Path,timezone='UTC',secret_root=None):
        self.root=Path(root)
        if self.root.resolve()!=self.root.absolute():raise BotError('unsafe_store')
        private_dir(self.root);self.timezone=ZoneInfo(timezone)
        p=self.root/'bots.sqlite3'
        if p.is_symlink():raise BotError('unsafe_store')
        fd=os.open(p,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        st=os.fstat(fd);os.close(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o077:raise BotError('unsafe_store')
        self.secret_root=Path(secret_root) if secret_root is not None else None
        self.db=sqlite3.connect(p,isolation_level=None,timeout=10)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY, config TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bot_lifecycle(bot TEXT PRIMARY KEY, epoch INTEGER NOT NULL, removed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS visitors(bot TEXT, id TEXT, capability TEXT NOT NULL, identity TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(bot,id));
        CREATE TABLE IF NOT EXISTS visitor_seen(bot TEXT, visitor TEXT, seen INTEGER, PRIMARY KEY(bot,visitor));
        CREATE TABLE IF NOT EXISTS visitor_bindings(bot TEXT, public_hash TEXT, visitor TEXT, seen INTEGER, PRIMARY KEY(bot,public_hash));
        CREATE TABLE IF NOT EXISTS turns(bot TEXT, visitor TEXT, request TEXT, day TEXT, status TEXT, answer TEXT, created INTEGER, PRIMARY KEY(bot,visitor,request));
        CREATE TABLE IF NOT EXISTS messages(bot TEXT, visitor TEXT, request TEXT, role TEXT, text TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, bot TEXT, visitor TEXT, request TEXT, day TEXT, reserved INTEGER, actual INTEGER, kind TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS tool_calls(bot TEXT, visitor TEXT, tool TEXT, day TEXT, calls INTEGER, PRIMARY KEY(bot,visitor,tool,day));
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, bot TEXT, visitor TEXT, request TEXT, digest TEXT, expires INTEGER, status TEXT);
        CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, bot TEXT, visitor TEXT, request TEXT, status TEXT, created INTEGER);
        CREATE TABLE IF NOT EXISTS activity(bot TEXT, visitor TEXT, kind TEXT, outcome TEXT, created INTEGER);
        ''')
        # DELETE journalling keeps auxiliary files ephemeral; no shared cloud database.
        self.db.execute('INSERT OR IGNORE INTO visitor_seen SELECT bot,id,? FROM visitors',(self.now(),))
        self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
    def close(self):self.db.close()
    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:yield;self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
    def now(self):return int(dt.datetime.now(dt.timezone.utc).timestamp())
    def day(self):return dt.datetime.fromtimestamp(self.now(),self.timezone).date().isoformat()
    def directory(self,bid):
        p=self.root/ident(bid);private_dir(p)
        for name in ('knowledge','tools','secrets'):private_dir(p/name)
        return p
    def secrets(self,bid):
        p=(self.secret_root/ident(bid)) if self.secret_root is not None else (self.directory(bid)/"secrets")
        if p.resolve()!=p.absolute():raise BotError("unsafe_secret_directory")
        private_dir(p);return p
    def get(self,bid):
        row=self.db.execute('SELECT config FROM bots WHERE id=?',(ident(bid),)).fetchone()
        if not row:raise BotError('unknown_bot')
        return json.loads(row[0])
    def lifecycle(self,bid):
        self.get(bid)
        row=self.db.execute('SELECT epoch,removed FROM bot_lifecycle WHERE bot=?',(bid,)).fetchone()
        return dict(row) if row else {'epoch':1,'removed':0}
    def listing(self,include_removed=False):
        return [{'id':r['id'],**json.loads(r['config'])} for r in self.db.execute('SELECT * FROM bots ORDER BY id') if include_removed or not self.lifecycle(r['id'])['removed']]
    def remove(self,bid):
        c=self.get(bid);c['enabled']=False;self.save(bid,c)
        self.db.execute('UPDATE bot_lifecycle SET removed=1 WHERE bot=?',(bid,));self.event(bid,'','config','removed')
        return {'id':bid,'removed':True}
    def save(self,bid,raw):
        bid=ident(bid);c=config(raw)
        with self.transaction():
            row=self.db.execute('SELECT removed FROM bot_lifecycle WHERE bot=?',(bid,)).fetchone()
            if row and row[0]:raise BotError('bot_removed')
            if self.db.execute("SELECT 1 FROM bots WHERE id!=? AND json_extract(config,'$.slug')=?",(bid,c['slug'])).fetchone():raise BotError('name_unavailable')
            if c['enabled'] and self.db.execute("SELECT count(*) FROM bots WHERE id!=? AND json_extract(config,'$.enabled')=1",(bid,)).fetchone()[0]:raise BotError('seat_bot_limit')
            self.db.execute('INSERT INTO bot_lifecycle VALUES(?,1,0) ON CONFLICT(bot) DO UPDATE SET epoch=epoch+1',(bid,))
            self.db.execute('INSERT INTO bots VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config',(bid,canonical(c)))
        self.directory(bid);self.event(bid,'','config','saved');return {'id':bid,**c}
    def create(self,raw):return self.save(secrets.token_hex(16),raw)
    def visitor(self,bid):
        self.get(bid);vid=secrets.token_hex(16);cap=secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO visitors(bot,id,capability) VALUES(?,?,?)',(bid,vid,hashlib.sha256(cap.encode()).hexdigest()))
        self.touch(bid,vid)
        return vid,cap
    def touch(self,bid,vid):
        self.db.execute('INSERT INTO visitor_seen VALUES(?,?,?) ON CONFLICT(bot,visitor) DO UPDATE SET seen=excluded.seen',(bid,vid,self.now()))
        self.db.execute('UPDATE visitor_bindings SET seen=? WHERE bot=? AND visitor=?',(self.now(),bid,vid))
    def admit_visitor(self,bid,public_hash):
        # Authenticated admission public key, never a visitor-supplied local ID. Inactivity archives the identity.
        ident(public_hash);self.get(bid)
        row=self.db.execute('SELECT visitor,seen FROM visitor_bindings WHERE bot=? AND public_hash=?',(bid,public_hash)).fetchone()
        if not row or row['seen']<=self.now()-86400:
            vid,cap=self.visitor(bid)
            self.db.execute('INSERT INTO visitor_bindings VALUES(?,?,?,?) ON CONFLICT(bot,public_hash) DO UPDATE SET visitor=excluded.visitor,seen=excluded.seen',(bid,public_hash,vid,self.now()))
        else:
            vid=row['visitor'];cap=secrets.token_urlsafe(32)
            self.db.execute('UPDATE visitors SET capability=? WHERE bot=? AND id=?',(hashlib.sha256(cap.encode()).hexdigest(),bid,vid));self.touch(bid,vid)
        return vid,cap
    def authenticate(self,bid,vid,cap):
        row=self.db.execute('SELECT capability FROM visitors WHERE bot=? AND id=?',(ident(bid),ident(vid))).fetchone()
        if not isinstance(cap,str) or len(cap)>64 or not row or not secrets.compare_digest(row[0],hashlib.sha256(cap.encode()).hexdigest()):raise BotError('visitor_auth')
        self.touch(bid,vid)
    def set_identity(self,bid,vid,raw):
        if not isinstance(raw,dict) or set(raw)-{'order_id','email'}:raise BotError('invalid_identity')
        out={}
        for k,v in raw.items():
            pattern=r'[A-Za-z0-9_-]{1,64}' if k=='order_id' else r'[A-Za-z0-9._+%-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,20}'
            if not isinstance(v,str) or not re.fullmatch(pattern,v):raise BotError('invalid_identity')
            out[k]=v
        self.touch(bid,vid)
        # Once fixed, the model cannot replace it; owner authenticated channel can clear the visitor.
        old=self.identity(bid,vid)
        if old and old!=out:raise BotError('identity_already_bound')
        if not self.db.execute('UPDATE visitors SET identity=? WHERE bot=? AND id=?',(canonical(out),bid,vid)).rowcount:raise BotError('visitor_auth')
    def identity(self,bid,vid):
        row=self.db.execute('SELECT identity FROM visitors WHERE bot=? AND id=?',(bid,vid)).fetchone()
        if not row:raise BotError('visitor_auth')
        return json.loads(row[0])
    def start(self,bid,vid,rid,text):
        ident(rid)
        if not isinstance(text,str) or not text or len(text)>2000:raise BotError('invalid_message')
        c=self.get(bid);day=self.day()
        if not c['enabled']:raise BotError('offline')
        now=dt.datetime.fromtimestamp(self.now(),ZoneInfo(c['timezone']));hours=c['hours']
        if hours is not None and (now.weekday() not in hours['days'] or not (hours['start']<=now.hour<hours['end'] if hours['start']<hours['end'] else now.hour>=hours['start'] or now.hour<hours['end'])):raise BotError('outside_hours')
        with self.transaction():
            prior=self.db.execute('SELECT status,answer FROM turns WHERE bot=? AND visitor=? AND request=?',(bid,vid,rid)).fetchone()
            if prior:return dict(prior)
            if self.db.execute("SELECT 1 FROM turns WHERE bot=? AND visitor=? AND status='running'",(bid,vid)).fetchone():raise BotError('session_busy')
            if self.db.execute("SELECT count(*) FROM turns WHERE status='running'").fetchone()[0]>=c['limits']['concurrency']:raise BotError('busy')
            checks=[('day=?',(day,),c['limits']['host_messages']),('day=? AND bot=?',(day,bid),c['limits']['bot_messages']),('day=? AND bot=? AND visitor=?',(day,bid,vid),c['limits']['visitor_messages']),('created>? AND bot=? AND visitor=?',(self.now()-60,bid,vid),6)]
            for where,args,maximum in checks:
                if self.db.execute('SELECT count(*) FROM turns WHERE '+where,args).fetchone()[0]>=maximum:raise BotError('message_limit')
            self.touch(bid,vid)
            self.db.execute('INSERT INTO turns VALUES(?,?,?,?,?,?,?)',(bid,vid,rid,day,'running',None,self.now()))
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(bid,vid,rid,'user',text,self.now()))
        return None
    def reserve(self,bid,vid,rid,maximum,kind,remaining=False):
        if type(maximum) is not int or maximum<1:raise BotError('invalid_reservation')
        day=self.day();call=secrets.token_hex(16)
        with self.transaction():
            used=self.db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM calls WHERE day=?',(day,)).fetchone()[0]
            ceiling=self.get(bid)['limits']['host_tokens']
            if remaining:
                if ceiling-used<maximum:raise BotError('token_limit')
                maximum=ceiling-used
            if used+maximum>ceiling:raise BotError('token_limit')
            self.db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',(call,bid,vid,rid,day,maximum,None,kind,self.now()))
        return call
    def settle(self,call,actual):
        if actual is not None and (type(actual) is not int or actual<0):raise BotError('invalid_usage')
        row=self.db.execute('SELECT reserved FROM calls WHERE id=?',(call,)).fetchone()
        if not row:raise BotError('unknown_call')
        if actual is not None:self.db.execute('UPDATE calls SET actual=? WHERE id=? AND actual IS NULL',(actual,call))
        if actual is not None and actual>row[0]:raise BotError('provider_exceeded_reservation')
    def finish(self,bid,vid,rid,answer,status='done'):
        with self.transaction():
            self.db.execute('UPDATE turns SET answer=?,status=? WHERE bot=? AND visitor=? AND request=? AND status=?',(answer,status,bid,vid,rid,'running'))
            if answer:self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(bid,vid,rid,'assistant',answer,self.now()))
        self.event(bid,vid,'turn',status)
    def history(self,bid,vid):
        return [dict(r) for r in self.db.execute('SELECT role,text FROM messages WHERE bot=? AND visitor=? AND created>? ORDER BY rowid DESC LIMIT 40',(bid,vid,self.now()-86400))][::-1]
    def recover(self):
        # No model is retried; its reservation remains. Call once at service startup.
        self.db.execute("UPDATE turns SET status='interrupted' WHERE status='running'")
        self.db.execute("UPDATE approvals SET status='expired' WHERE status='pending'")
    def prune(self):
        cutoff=self.now()-30*86400
        with self.transaction():
            self.db.execute('DELETE FROM visitors WHERE (bot,id) IN (SELECT bot,visitor FROM visitor_seen WHERE seen<?)',(cutoff,))
            self.db.execute('DELETE FROM visitor_seen WHERE seen<?',(cutoff,))
            self.db.execute('DELETE FROM visitor_bindings WHERE seen<?',(cutoff,))
            for table in ('messages','turns','calls','activity','handoffs'):self.db.execute('DELETE FROM '+table+' WHERE created<?',(cutoff,))
            self.db.execute('DELETE FROM approvals WHERE expires<?',(cutoff,))
            self.db.execute('DELETE FROM tool_calls WHERE day<?',(dt.datetime.fromtimestamp(cutoff,self.timezone).date().isoformat(),))
    def event(self,bid,vid,kind,outcome):
        self.db.execute('INSERT INTO activity VALUES(?,?,?,?,?)',(bid,vid,kind,outcome,self.now()))
    def statistics(self,bid):
        self.get(bid)
        return [dict(r) for r in self.db.execute('SELECT day,count(*) calls,sum(coalesce(actual,reserved)) charged_tokens,sum(actual) actual_tokens,sum(actual IS NULL) unknown_calls FROM calls WHERE bot=? GROUP BY day ORDER BY day DESC LIMIT 30',(bid,))]
