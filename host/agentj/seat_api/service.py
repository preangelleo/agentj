"""Strict non-streaming bot subset, sandboxed workflows, local jobs and owner-independent caller errors."""
import asyncio
import json
import re
import secrets
from ..bots.store import BotError,canonical
from ..bots.engine import Engine
from ..bots.tools import ToolRegistry
from . import e2e
from .store import QUEUE_SECONDS

STATUS={'key_invalid':401,'key_scope':403,'api_disabled':404,'unknown_endpoint':404,'unknown_request':404,'request_expired':410,
        'rest_disclosure_required':403,'e2e_required':403,'idempotency_conflict':409,'contract_changed':409,'contract_unavailable':503,'idempotency_required':422,
        'invalid_input':422,'schema_mismatch':422,'unsupported':422,'too_large':413,'rate_limit':429,'token_limit':429,'concurrency_limit':429,'queue_full':429,
        'queue_timeout':503,'offline':503,'native_unqualified':503,'workflow_unsupported':503,'safety_unavailable':503,'deadline':504,'request_deleted':410,
        'invalid_envelope':400,'replay':409,'route_epoch':409,'not_found':404,'endpoint_migrating':503,'endpoint_migrated':410,'endpoint_taken_down':410,
        'provision_limit':429,'invalid_expiry':422,'unknown_key':404,'workflow_failed':502,'workflow_output_limit':502,'workflow_bad_output':502,
        'unsafe_output':502,'safety_refused':502,'unknown_callback':422}
class KeyBudget:
    def __init__(self,store,key):self.store=store;self.key=key;self.bots=store.bots
    def __getattr__(self,k):return getattr(self.bots,k)
    def reserve(self,bid,vid,rid,maximum,kind,remaining=False):
        limits=json.loads(self.key['limits'])
        used=self.db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM calls WHERE visitor IN (SELECT visitor FROM api_sessions WHERE key_id=?) AND day=?',(self.key['id'],self.day())).fetchone()[0]
        available=limits['tokens']-used
        if maximum>available:raise BotError('token_limit')
        if remaining:
            host_used=self.db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM calls WHERE day=?',(self.day(),)).fetchone()[0]
            maximum=min(available,self.get(bid)['limits']['host_tokens']-host_used)
        return self.bots.reserve(bid,vid,rid,maximum,kind)

class Service:
    def __init__(self,store,engine_factory=None,online=lambda:True,account=lambda eid:None,workflow_run=None,authority=None):
        self.authority=authority
        self.store=store;self.online=online;self.account=account;self.jobs={};self.slot=asyncio.Event()
        self.engine_factory=engine_factory or (lambda budget:Engine(budget,None,queue_timeout=0))
        if workflow_run is None:
            from . import workflow
            workflow_run=workflow.run
        self.workflow_run=workflow_run
        store.recover()
    def input(self,e,mode,b):
        if not isinstance(b,dict):raise BotError('invalid_input')
        callback=None
        if mode=='chat/completions':
            if e['workflow']:raise BotError('unsupported')
            if set(b)-{'model','messages','stream'} or b.get('model')!=e['public_id'] or b.get('stream',False) is not False:raise BotError('unsupported')
            messages=b.get('messages')
            # Caller cannot inject system/developer/tools. No arbitrary models or remote attachments.
            if not isinstance(messages,list) or not 1<=len(messages)<=20:raise BotError('invalid_input')
            for m in messages:
                if not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in ('user','assistant') or not isinstance(m['content'],str):raise BotError('unsupported')
            if messages[-1]['role']!='user':raise BotError('invalid_input')
            text='\n'.join(m['role']+': '+m['content'] for m in messages);conversation=secrets.token_hex(16)
        else:
            if set(b)-{'input','conversation_id','callback_id'} or not isinstance(b.get('input'),dict):raise BotError('invalid_input')
            if 'callback_id' in b:
                callback=b['callback_id']
                if mode!='jobs' or not isinstance(callback,str) or not self.store.db.execute("SELECT 1 FROM api_callbacks WHERE id=? AND endpoint=? AND kind='result' AND enabled=1",(callback,e['id'])).fetchone():raise BotError('unknown_callback')
            if e['workflow']:
                if 'conversation_id' in b:raise BotError('unsupported')
                text=canonical(b['input']);conversation=secrets.token_hex(16)
            else:
                if set(b['input'])!={'message'}:raise BotError('invalid_input')
                text=b['input']['message'];conversation=b.get('conversation_id') or secrets.token_hex(16)
        if not isinstance(text,str) or not 1<=len(text)<=(16000 if e['workflow'] else 2000):raise BotError('invalid_input')
        from .. import privacy
        if any(pattern.search(text) for _,pattern,_ in privacy._SECRETS):raise BotError('invalid_input')
        return text,conversation,callback
    async def wait_slot(self,rid):
        """Seat-wide queue: 20 waiting, 30 s each, 4 shared bot/API slots. Expiry never runs the request later."""
        loop=asyncio.get_running_loop();deadline=loop.time()+QUEUE_SECONDS
        while not self.store.try_start(rid):
            if self.store.status(rid)!='queued':raise BotError('request_deleted')
            if loop.time()>=deadline:raise BotError('queue_timeout')
            self.slot.clear()
            try:await asyncio.wait_for(self.slot.wait(),min(0.25,max(0.01,deadline-loop.time())))
            except TimeoutError:pass
    def released(self):self.slot.set()
    async def dispatch(self,eid,raw,method,path,body=None,idem=None,encrypted=False):
        try:
            if not self.online():raise BotError('offline')
            capture=self.authority(eid) if self.authority is not None else None
            if self.authority is not None and capture is None:raise BotError('offline')
            if len(canonical(body).encode())>65536:raise BotError('too_large')
            if not encrypted and path in ('_e2e','_provision'):return await self.envelope(eid,method,path,body)
            if not encrypted and path=='openapi.json':return self.public_spec(eid,method)
            key=self.store.authenticate(eid,raw);e=self.store.endpoint(eid)
            if e['privacy']=='e2e' and not encrypted:raise BotError('e2e_required')
            if path.startswith('jobs/'):
                rid=path[5:];r=self.store.result(eid,key,rid)
                if self.authority is not None and r['authority']!=canonical(capture):raise BotError('offline')
                if method=='DELETE':
                    task=self.jobs.get(rid)
                    if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
                    self.store.erase(rid)
                    r=self.store.result(eid,key,rid)
                elif method!='GET':raise BotError('unsupported')
                return 200,{'job_id':r['id'],'status':r['status'],'expires_at':r['expires'],'result':json.loads(r['response']) if r['response'] else None}
            if method!='POST' or path not in ('','chat/completions','jobs'):raise BotError('unsupported')
            text,conversation,callback=self.input(e,path,body)
            if e['workflow']:
                from . import workflow
                # Contract input schema is checked before admission; nothing invalid is queued or billed.
                workflow.validate(workflow.load(self.store.workflow_root,e['workflow'])['input_schema'],body['input'])
            # This executor has no owner approval callback and only immutable read tools.
            if any(t.get('enabled') and t.get('level')!='read' for t in ToolRegistry(self.store.bots,e['bot']).listing()):raise BotError('unsupported')
            r,new=self.store.admit(eid,key,method+':'+path,idem,body,callback)
            if not new:
                if self.authority is not None and r['authority']!=canonical(capture):raise BotError('offline')
                if r['status']=='deleted':raise BotError('request_deleted')
                if path=='jobs':return 202,{'job_id':r['id'],'status':r['status'],'expires_at':r['expires']}
                if r['response']:return (200 if r['status']=='succeeded' else 503),json.loads(r['response'])
                return 409,{'error':{'code':'request_pending'},'request_id':r['id']}
            if self.authority is not None:self.store.db.execute('UPDATE api_requests SET authority=? WHERE id=?',(canonical(capture),r['id']))
            vid,cap=self.store.session(eid,key,conversation)
            async def execute():
                try:
                    await self.wait_slot(r['id'])
                    self.store.authenticate(eid,raw)
                    if self.authority is not None and self.authority(eid)!=capture:raise BotError('offline')
                    engine=self.engine_factory(KeyBudget(self.store,key))
                    timeout=600 if path=='jobs' else 30
                    if e['workflow']:result=await asyncio.wait_for(self.run_workflow(engine,e,vid,r['id'],body['input'],timeout),timeout)
                    else:result=await asyncio.wait_for(engine.reply(e['bot'],vid,cap,r['id'],text),timeout)
                    # Authenticate again immediately before output; revoke/pause/change cannot release results.
                    self.store.authenticate(eid,raw)
                    if not self.online() or (self.authority is not None and self.authority(eid)!=capture):raise BotError('offline')
                    if result['status']!='done':raise BotError('safety_unavailable')
                    usage=self.store.db.execute('SELECT sum(actual) actual,sum(actual IS NULL) unknown FROM calls WHERE request=?',(r['id'],)).fetchone()
                    total=usage['actual'] if not usage['unknown'] else None
                    if e['workflow']:
                        answer={'request_id':r['id'],'output':result['output'],'files':result['files'],'usage':{'total_tokens':total,'input_tokens':None,'output_tokens':None},'contract_version':e['version']}
                    else:
                        # Native adapters report total usage, not input/output breakdown. Keep unknowns null.
                        answer={'request_id':r['id'],'output':{'message':result['text']},'usage':{'total_tokens':total,'input_tokens':None,'output_tokens':None},'contract_version':e['version']}
                    if path=='chat/completions':answer={'id':'chatcmpl-'+r['id'],'object':'chat.completion','created':self.store.now(),'model':e['public_id'],
                      'choices':[{'index':0,'message':{'role':'assistant','content':result['text']},'finish_reason':'stop'}],
                      'usage':{'prompt_tokens':None,'completion_tokens':None,'total_tokens':total}}
                    if not self.store.finish(r['id'],answer,'succeeded'):raise BotError('request_expired')
                    return 200,answer
                except asyncio.CancelledError:
                    self.store.finish(r['id'],{'error':{'code':'cancelled'}},'cancelled');raise
                except Exception as exc:
                    code=str(exc) if isinstance(exc,BotError) and str(exc) in STATUS else 'deadline' if isinstance(exc,TimeoutError) else 'safety_unavailable'
                    error={'error':{'code':code},'request_id':r['id']};self.store.finish(r['id'],error,'expired' if code=='queue_timeout' else 'failed');return STATUS[code],error
                finally:self.released()
            if path=='jobs':
                task=asyncio.create_task(execute());self.jobs[r['id']]=task;task.add_done_callback(lambda _:self.jobs.pop(r['id'],None))
                return 202,{'job_id':r['id'],'status':'queued','expires_at':r['expires']}
            return await execute()
        except Exception as exc:
            code=str(exc) if isinstance(exc,BotError) and str(exc) in STATUS else 'invalid_input'
            return STATUS.get(code,422),{'error':{'code':code}}
    async def run_workflow(self,engine,e,vid,rid,value,timeout):
        """Inbound policy, sandboxed program, deterministic scan and outbound Jev on the whole output object."""
        c=self.store.bots.get(e['bot']);p=engine.provider_resolve(c['provider'],engine.harness,engine.model)
        from . import workflow
        published=json.loads(e['contract']).get('workflow')
        freezing=asyncio.create_task(asyncio.to_thread(workflow.freeze,self.store.workflow_root,e['workflow'],published))
        try:stage=await asyncio.shield(freezing)
        except asyncio.CancelledError:
            # A cancelled to_thread still copies: retrieve and clean its eventual stage.
            try:
                abandoned=await freezing
                await asyncio.to_thread(abandoned.cleanup)
            except Exception:pass
            raise
        try:
            if not await engine.safety(p,e['bot'],vid,rid,canonical(value),'inbound'):raise BotError('safety_refused')
            out=await self.workflow_run(stage.name,e['workflow'],value,timeout)
            if not await engine.safety(p,e['bot'],vid,rid,canonical(out),'outbound'):raise BotError('safety_refused')
            return {'status':'done',**out}
        finally:await asyncio.to_thread(stage.cleanup)
    def public_spec(self,eid,method):
        e=self.store.endpoint(eid);account=self.account(eid)
        if method!='GET' or not e['public_spec'] or not e['enabled'] or not self.store.enabled() or not account:raise BotError('not_found')
        from .spec import generate
        return 200,generate(e,account,self.store)
    async def envelope(self,eid,method,path,env):
        e=self.store.endpoint(eid)
        if not self.store.enabled() or not e['enabled']:raise BotError('api_disabled')
        if e['state']!='active':raise BotError('endpoint_'+e['state'])
        keys=self.store.secret(eid)
        if path=='_e2e' and method=='GET':return 200,e2e.manifest(keys,e,self.store.now())
        if method!='POST':raise BotError('unsupported')
        e2e.check_header(env,e,self.store.now())
        inner,resp=e2e.open_request(keys,env)
        self.store.seen(env['key_id'],env['request_id'],env['deadline'])
        if path=='_provision':
            try:status,body=200,self.store.provision(eid,env['key_id'],inner)
            except BotError as exc:
                code=str(exc) if str(exc) in STATUS else 'invalid_input';status,body=STATUS.get(code,422),{'error':{'code':code}}
            return 200,e2e.seal_response(resp,env['request_id'],status,body)
        if set(inner)!={'key','method','path','body','idem'}:raise BotError('invalid_envelope')
        m=re.fullmatch(r'ajapi_([a-f0-9]{32})_([a-f0-9]{32})_[A-Za-z0-9_-]{43}',inner['key']) if isinstance(inner['key'],str) else None
        # The outer key_id the edge saw must name the same key as the encrypted secret.
        if not m or m.group(2)!=env['key_id'] or inner['path'] in ('_e2e','_provision','openapi.json') or not isinstance(inner['path'],str):
            return 200,e2e.seal_response(resp,env['request_id'],401,{'error':{'code':'key_invalid'}})
        status,body=await self.dispatch(eid,inner['key'],inner['method'],inner['path'],inner['body'],inner['idem'],encrypted=True)
        return 200,e2e.seal_response(resp,env['request_id'],status,body)
    async def close(self):
        tasks=list(self.jobs.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
