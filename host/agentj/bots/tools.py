"""Declarative HTTP tools; no shell, arbitrary paths, model-supplied identities or URLs.

Write tools require an owner-phone approval bound to the exact call digest and TTL.
Only selected response fields are returned to the model. Activity contains metadata.
"""
from __future__ import annotations
import hashlib
import asyncio
import json
import re
import secrets
from urllib.parse import quote,urlsplit
from ..provider_profiles import atomic,secure_read
from .. import privacy
from .store import BotError,canonical
from .network import request_json

NAME=re.compile(r'[a-z][a-z0-9_]{0,39}')
REFERENCE_NAME_PATTERN=re.compile(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}')

def schema(s,depth=0):
    if depth>4 or not isinstance(s,dict) or set(s)-{'type','properties','required','additionalProperties','items','enum','minLength','maxLength','minimum','maximum','maxItems','description'}:raise BotError('invalid_schema')
    typ=s.get('type')
    if typ not in ('string','integer','number','boolean','object','array'):raise BotError('invalid_schema')
    if 'enum' in s and (not isinstance(s['enum'],list) or not 1<=len(s['enum'])<=50):raise BotError('invalid_schema')
    if typ=='object':
        props=s.get('properties',{})
        if not isinstance(props,dict) or len(props)>20 or s.get('additionalProperties') is not False:raise BotError('invalid_schema')
        required=s.get('required',[])
        if not isinstance(required,list) or any(not isinstance(k,str) or k not in props for k in required):raise BotError('invalid_schema')
        for k,v in props.items():
            if not isinstance(k,str) or not NAME.fullmatch(k):raise BotError('invalid_schema')
            schema(v,depth+1)
    if typ=='string' and (type(s.get('maxLength')) is not int or not 1<=s['maxLength']<=2000):raise BotError('invalid_schema')
    if typ=='array':
        if type(s.get('maxItems')) is not int or not 1<=s['maxItems']<=20:raise BotError('invalid_schema')
        schema(s.get('items'),depth+1)
    for k in ('minimum','maximum','minLength','maxLength','maxItems'):
        if k in s and (type(s[k]) not in (int,float) or not -1e12<=s[k]<=1e12):raise BotError('invalid_schema')
    return s

def validate(s,value):
    typ=s['type'];types={'string':str,'integer':int,'number':(int,float),'boolean':bool,'object':dict,'array':list}
    if not isinstance(value,types[typ]) or typ in ('integer','number') and isinstance(value,bool):raise BotError('invalid_args')
    if 'enum' in s and value not in s['enum']:raise BotError('invalid_args')
    if typ=='string' and (not s.get('minLength',0)<=len(value)<=s['maxLength'] or any(ord(c)<32 for c in value)):raise BotError('invalid_args')
    if typ in ('integer','number'):
        import math
        if not math.isfinite(value) or not s.get('minimum',-1e12)<=value<=s.get('maximum',1e12):raise BotError('invalid_args')
    if typ=='object':
        if set(value)-set(s['properties']) or set(s.get('required',[]))-set(value):raise BotError('invalid_args')
        for k,v in value.items():validate(s['properties'][k],v)
    if typ=='array':
        if len(value)>s['maxItems']:raise BotError('invalid_args')
        for v in value:validate(s['items'],v)
    return value

def definition(raw,domains):
    keys={'name','description','level','enabled','method','url','parameters','visitor_bindings','auth','timeout','visitor_limit','daily_limit','response_fields','response_chars'}
    if not isinstance(raw,dict) or set(raw)-keys:raise BotError('invalid_tool')
    d={'level':'read','enabled':False,'timeout':10,'visitor_limit':10,'daily_limit':100,'response_chars':4000,'visitor_bindings':{},'auth':None,**raw}
    if not isinstance(d.get('name'),str) or not NAME.fullmatch(d['name']) or not isinstance(d.get('description'),str) or not 1<=len(d['description'])<=1000:raise BotError('invalid_tool')
    if d['level'] not in ('read','write') or type(d['enabled']) is not bool or d.get('method') not in ('GET','POST','PUT','PATCH','DELETE'):raise BotError('invalid_tool')
    if d['level']=='read' and d['method']!='GET':raise BotError('write_method_requires_approval')
    schema(d.get('parameters'))
    if d['parameters']['type']!='object':raise BotError('invalid_schema')
    bindings=d['visitor_bindings']
    if not isinstance(bindings,dict) or any(k not in d['parameters']['properties'] or v not in ('order_id','email') for k,v in bindings.items()):raise BotError('invalid_visitor_binding')
    url=d.get('url')
    if not isinstance(url,str) or len(url)>2000:raise BotError('unsafe_url')
    try:u=urlsplit(url)
    except ValueError:raise BotError('unsafe_url') from None
    if u.scheme!='https' or u.hostname not in domains or u.username or u.password or u.port not in (None,443) or u.fragment or '{' in u.netloc or '}' in u.netloc:raise BotError('domain_not_allowed')
    names=re.findall(r'\{([a-z][a-z0-9_]*)\}',url)
    if any(k not in d['parameters']['properties'] for k in names) or '{' in re.sub(r'\{[a-z][a-z0-9_]*\}','',url):raise BotError('invalid_url_template')
    for k,maximum in [('timeout',30),('visitor_limit',100),('daily_limit',10000),('response_chars',8000)]:
        if type(d[k]) is not int or not 1<=d[k]<=maximum:raise BotError('invalid_tool')
    fields=d.get('response_fields')
    if not isinstance(fields,list) or not 1<=len(fields)<=20 or any(not isinstance(f,str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){0,4}',f) for f in fields):raise BotError('invalid_response_fields')
    auth=d['auth']
    if auth is not None and (not isinstance(auth,dict) or set(auth)!={'secret','header','prefix'} or not isinstance(auth['secret'],str) or not REFERENCE_NAME_PATTERN.fullmatch(auth['secret']) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',auth.get('header','')) or auth['header'].lower() in ('host','content-length','connection','transfer-encoding') or auth.get('prefix') not in ('','Bearer ','Basic ')):raise BotError('invalid_auth_reference')
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,dict):
            for item in value.values():yield from strings(item)
        elif isinstance(value,list):
            for item in value:yield from strings(item)
    # Scan model-visible values, not auth-reference field labels ("secret" is a reference, not a credential).
    if any(pattern.search(value) for value in strings([d['description'],d['url'],d['parameters']]) for _,pattern,_ in privacy._SECRETS):raise BotError('tool_contains_secret')
    return d

class ToolRegistry:
    def __init__(self,store,bid,transport=request_json):self.store=store;self.bid=bid;self.root=store.directory(bid);self.transport=transport
    def load(self,name):
        if not isinstance(name,str) or not NAME.fullmatch(name):raise BotError('unknown_tool')
        try:raw=json.loads(secure_read(self.root/'tools'/(name+'.json'),65536))
        except (OSError,ValueError):raise BotError('unknown_tool') from None
        d=definition(raw,self.store.get(self.bid)['outbound_domains'])
        if d['name']!=name:raise BotError('invalid_tool')
        return d
    def save(self,raw):
        d=definition(raw,self.store.get(self.bid)['outbound_domains']);atomic(self.root/'tools'/(d['name']+'.json'),canonical(d).encode());return d
    def listing(self):return [self.load(p.stem) for p in sorted((self.root/'tools').glob('*.json'))]
    def offered(self):
        offered=[]
        for d in self.listing():
            if not d['enabled']:continue
            # Identity is injected from the authenticated visitor record, never requested from the model.
            bound=set(d['visitor_bindings'])
            schema={**d['parameters'],'properties':{k:v for k,v in d['parameters']['properties'].items() if k not in bound},
                    'required':[k for k in d['parameters'].get('required',[]) if k not in bound]}
            offered.append({'type':'function','function':{'name':d['name'],'description':d['description'],'parameters':schema}})
        return offered
    def prepare(self,vid,name,args):
        d=self.load(name)
        if not d['enabled']:raise BotError('tool_disabled')
        if not isinstance(args,dict):raise BotError('invalid_args')
        args=dict(args);identity=self.store.identity(self.bid,vid)
        for parameter,source in d['visitor_bindings'].items():
            if source not in identity:raise BotError('visitor_identity_required')
            if parameter in args and args[parameter]!=identity[source]:raise BotError('visitor_identity_mismatch')
            args[parameter]=identity[source]
        validate(d['parameters'],args)
        digest=hashlib.sha256(canonical({'bot':self.bid,'visitor':vid,'tool':d,'args':args}).encode()).hexdigest()
        return d,args,digest
    def approval(self,vid,rid,name,args):
        d,args,digest=self.prepare(vid,name,args)
        if d['level']!='write':raise BotError('approval_not_needed')
        aid=secrets.token_hex(16)
        self.store.db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(aid,self.bid,vid,rid,digest,self.store.now()+120,'pending'))
        return {'id':aid,'tool':name,'digest':digest,'expires':self.store.now()+120,'visitor':vid,'request':rid}
    def decide(self,aid,digest,allowed):
        # Called only by the signed owner handler after reviewing the immutable call.
        if type(allowed) is not bool:raise BotError('invalid_approval')
        changed=self.store.db.execute("UPDATE approvals SET status=? WHERE id=? AND bot=? AND digest=? AND status='pending' AND expires>?",('approved' if allowed else 'denied',aid,self.bid,digest,self.store.now())).rowcount
        if not changed:raise BotError('approval_expired')
    def _request(self,vid,rid,name,args,approval_id=None):
        d,args,digest=self.prepare(vid,name,args);day=self.store.day()
        with self.store.transaction():
            if d['level']=='write':
                changed=self.store.db.execute("UPDATE approvals SET status='consumed' WHERE id=? AND bot=? AND visitor=? AND request=? AND digest=? AND status='approved' AND expires>?",(approval_id,self.bid,vid,rid,digest,self.store.now())).rowcount
                if not changed:raise BotError('owner_approval_required')
            count=self.store.db.execute('SELECT coalesce(sum(calls),0) FROM tool_calls WHERE bot=? AND tool=? AND day=?',(self.bid,name,day)).fetchone()[0]
            own=self.store.db.execute('SELECT calls FROM tool_calls WHERE bot=? AND visitor=? AND tool=? AND day=?',(self.bid,vid,name,day)).fetchone()
            if count>=d['daily_limit'] or own and own[0]>=d['visitor_limit']:raise BotError('tool_limit')
            self.store.db.execute('INSERT INTO tool_calls VALUES(?,?,?,?,1) ON CONFLICT(bot,visitor,tool,day) DO UPDATE SET calls=calls+1',(self.bid,vid,name,day))
        url=d['url']
        for k,v in args.items():url=url.replace('{'+k+'}',quote(str(v),safe=''))
        if '{' in url or '}' in url:raise BotError('missing_url_arg')
        headers={}
        if d['auth']:
            a=d['auth']
            try:secret=secure_read(self.store.secrets(self.bid)/a['secret'],8192).decode().strip()
            except (OSError,ValueError):raise BotError('tool_secret_unavailable') from None
            if not secret or '\n' in secret or '\r' in secret:raise BotError('tool_secret_unavailable')
            headers[a['header']]=a['prefix']+secret
        return d,args,url,headers,secret if d['auth'] else None,list(self.store.get(self.bid)['outbound_domains'])
    def _result(self,vid,d,raw,secret):
        try:
            selected={}
            for path in d['response_fields']:
                value=raw
                for field in path.split('.'):
                    if not isinstance(value,dict) or field not in value:value=None;break
                    value=value[field]
                # Structured values cannot smuggle undeclared nested fields.
                if isinstance(value,(str,int,float,bool)) or value is None:selected[path]=value
            out=canonical(selected)[:d['response_chars']]
            if d['auth'] and secret in out:raise BotError('secret_in_response')
            self.store.event(self.bid,vid,'tool','ok');return out
        except BotError:self.store.event(self.bid,vid,'tool','refused');raise

    def execute(self,vid,rid,name,args,approval_id=None):
        d,args,url,headers,secret,domains=self._request(vid,rid,name,args,approval_id)
        try:
            raw=self.transport(url,d['method'],args if d['method']!='GET' else None,headers,timeout=d['timeout'],domains=domains)
        except BotError:self.store.event(self.bid,vid,'tool','refused');raise
        return self._result(vid,d,raw,secret)
    async def execute_async(self,vid,rid,name,args,approval_id=None):
        # SQLite and authorization stay on the host loop. Only bounded HTTPS runs in a thread.
        d,args,url,headers,secret,domains=self._request(vid,rid,name,args,approval_id)
        try:
            raw=await asyncio.to_thread(self.transport,url,d['method'],args if d['method']!='GET' else None,headers,timeout=d['timeout'],domains=domains)
        except BotError:self.store.event(self.bid,vid,'tool','refused');raise
        return self._result(vid,d,raw,secret)
