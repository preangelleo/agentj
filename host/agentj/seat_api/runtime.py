"""Independent REST host transport. Only API caller data reaches Service, never Host.app."""
import asyncio
import contextlib
import json
import time
from urllib.parse import urlsplit
from websockets.asyncio.client import connect
from .. import cloud,wire,credential_revoke
from ..bots.store import BotError,canonical
from ..bots.engine import Engine
from .service import Service

LEASE=60
def transport_url(base,eid):
    from ..bots.store import ident
    try:u=urlsplit(base)
    except (ValueError,TypeError):raise BotError('unsafe_transport') from None
    if u.scheme!='wss' or not u.hostname or u.username or u.password or u.query or u.fragment:raise BotError('unsafe_transport')
    return base.rstrip('/')+'/seat-api/'+ident(eid)+'/h'

class Runtime:
    def __init__(self,manager):
        self.manager=manager;self.host=manager.host;self.tasks={};self.receipts={};self.service=None;self.post=None
    def engine(self,budget):
        agent=self.host.agent
        from ..bots.provider import resolve
        def contract_provider(selection,harness,current_model=None):
            p=resolve(selection,harness,current_model)
            if p.api=='native':raise BotError('native_unqualified')
            e=budget.store.endpoint(budget.key['endpoint'])
            actual={'base_url':p.base_url,'model':p.model,'api':p.api}
            if e['snapshot']!=budget.store.snapshot(e['bot'],resolved_profile=actual,workflow=e['workflow']):raise BotError('contract_changed')
            return p
        return Engine(budget,getattr(agent,'kind',None) or (self.host.agent_cfg or {}).get('kind'),agent.cur_model() if agent and hasattr(agent,'cur_model') else None,provider_resolve=contract_provider,queue_timeout=0)
    def local_binding(self):
        link=cloud.read_cloud(self.host.st)
        if not link:raise BotError('offline')
        return {'api':cloud.api_url(self.host.st,link),'host_id':link['host_id'],
                'tenant':link['tenant']['slug'],'linked_at':link.get('linked_at',0),
                'credential_epoch':credential_revoke.read(self.host.st)['epoch']}
    def online(self):
        try:return bool(self.local_binding()) and not self.host.stopped()
        except (OSError,ValueError,BotError):return False
    def capture(self,eid):
        if not self.lease_fresh(eid):return None
        api=self.manager.api_state();e=api.endpoint(eid)
        api.require_qualified(e)
        return {'binding':self.local_binding(),'cloud_binding':self.receipts[eid]['cloud_binding'],
                'route_epoch':e['epoch'],'contract':e['snapshot'],
                'lifecycle':self.manager.api_state().bots.lifecycle(e['bot'])}

    def lease_fresh(self,eid):
        """Outbound gate: a takedown or migration reaches the host within one lease; stale lease = no callback."""
        r=self.receipts.get(eid)
        try:return bool(r and 0<=time.monotonic()-r['monotonic']<LEASE and self.online()
                        and r['local_binding']==self.local_binding())
        except (OSError,ValueError,BotError):return False
    async def signed(self,path,context,t,endpoint):
        binding=self.local_binding()
        self.seq=max(getattr(self,'seq',0)+1,time.time_ns()//1000000)
        inner={'v':1,'t':t,'channel':self.host.channel,'ts':int(time.time()),'seq':self.seq,
               'credential_epoch':binding['credential_epoch'],'endpoint':endpoint}
        return await asyncio.to_thread(cloud.post_json,binding['api']+path,cloud.envelope(context,inner,self.host.sk))
    async def register(self,eid):
        api=self.manager.api_state();e=api.endpoint(eid)
        endpoint={'id':e['id'],'public_id':e['public_id'],'epoch':e['epoch'],'enabled':bool(api.enabled() and e['enabled'] and e['state']=='active'),'privacy':e['privacy']}
        before=self.local_binding()
        code,value=await self.signed('/v1/host/seat-api/register','agentjarvis-host-seat-api-register-v1','seat_api_register',endpoint)
        if code==451:
            # Metadata-only takedown: stop serving and stop callbacks; owner data stays on this computer.
            api.db.execute("UPDATE api_endpoints SET state='taken_down',enabled=0 WHERE id=?",(eid,));self.receipts.pop(eid,None)
            raise BotError('endpoint_taken_down')
        if code!=200 or not isinstance(value.get('ticket'),dict):raise BotError('registration_unavailable')
        binding=value.get('binding')
        if (before!=self.local_binding() or not isinstance(binding,dict)
                or binding.get('host_id')!=before['host_id']
                or binding.get('credential_epoch')!=before['credential_epoch']
                or binding.get('host_pk')!=wire.b64u(self.host.st.signing_pub())
                or not isinstance(binding.get('tenant_id'),str)):
            self.receipts.pop(eid,None);raise BotError('binding_changed')
        self.receipts[eid]={'account_name':value['account_name'],'namespace_version':value['namespace_version'],
                            'monotonic':time.monotonic(),'local_binding':before,'cloud_binding':binding}
        return value['ticket']
    async def migrate(self,eid,from_epoch,to_host_pk):
        code,_=await self.signed('/v1/host/seat-api/migrate','agentjarvis-host-seat-api-migrate-v1','seat_api_migrate',{'id':eid,'from_epoch':from_epoch,'to_host_pk':to_host_pk})
        if code!=200:raise BotError('migration_route_refused')
        self.receipts.pop(eid,None)
    def account(self,eid):
        r=self.receipts.get(eid);return r['account_name'] if r else None
    def callback_live(self,api,d,capture):
        # Executed in the outbound worker immediately before the HTTP body send.
        # A fresh read-only connection avoids using the event-loop SQLite handle.
        import sqlite3
        try:
            if not self.lease_fresh(d['endpoint']) or self.local_binding()!=capture['binding']:return False
            with contextlib.closing(sqlite3.connect((api.bots.root/'bots.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                e=db.execute('SELECT * FROM api_endpoints WHERE id=?',(d['endpoint'],)).fetchone()
                cb=db.execute('SELECT enabled FROM api_callbacks WHERE id=?',(d['callback'],)).fetchone()
                row=db.execute('SELECT authority FROM api_deliveries WHERE id=? AND status=?',(d['id'],'pending')).fetchone()
                life=db.execute('SELECT epoch,removed FROM bot_lifecycle WHERE bot=?',(e['bot'],)).fetchone() if e else None
                if (not e or not cb or not cb['enabled'] or not row or row['authority']!=canonical(capture)
                        or not db.execute('SELECT enabled FROM api_state').fetchone()[0]
                        or not e['enabled'] or e['state']!='active' or e['snapshot']!=capture['contract']
                        or e['epoch']!=capture['route_epoch'] or not life
                        or dict(life)!=capture['lifecycle'] or life['removed']):return False
                if d['key_id']:
                    key=db.execute('SELECT * FROM api_keys WHERE id=?',(d['key_id'],)).fetchone()
                    if not key or key['revoked'] or key['expires']<=time.time() or key['version']!=e['version']:return False
            return True
        except (OSError,ValueError,KeyError,sqlite3.Error,BotError):return False
    async def deliver(self,api):
        from . import callbacks
        for d in api.due():
            # Revoked keys, paused/removed endpoints and stale leases never send.
            e=api.endpoint(d['endpoint'])
            key=api.db.execute('SELECT revoked FROM api_keys WHERE id=?',(d['key_id'],)).fetchone() if d['key_id'] else None
            if not e['enabled'] or e['state']!='active' or (d['key_id'] and (not key or key['revoked'])):
                api.db.execute("UPDATE api_deliveries SET status='cancelled',body=NULL WHERE id=?",(d['id'],));continue
            capture=self.capture(d['endpoint'])
            # Persistent deliveries keep the original job's authority, never a new Manager snapshot.
            if capture is None:continue
            if not d.get('authority') or d['authority']!=canonical(capture):
                api.db.execute("UPDATE api_deliveries SET status='cancelled',body=NULL WHERE id=?",(d['id'],));continue
            gate=lambda:self.callback_live(api,d,capture)
            ok=await asyncio.to_thread(self.post or callbacks.post,d['url'],bytes(d['secret']),d['event_id'],d['body'].encode(),before_send=gate)
            api.delivered(d['id'],ok)
    async def run(self):
        try:
            while True:
                api=self.manager.api_store
                wanted={e['id'] for e in api.metadata()['endpoints'] if e['enabled'] and e['state']=='active'} if api and api.enabled() and self.online() else set()
                for eid in list(self.tasks):
                    if eid not in wanted or self.tasks[eid].done():
                        self.tasks[eid].cancel();await asyncio.gather(self.tasks.pop(eid),return_exceptions=True)
                if api:
                    api.prune()
                    if self.service is None:self.service=Service(api,self.engine,self.online,self.account,authority=self.capture)
                    with contextlib.suppress(Exception):await self.deliver(api)
                for eid in wanted:
                    if eid not in self.tasks:self.tasks[eid]=asyncio.create_task(self.serve(eid))
                await asyncio.sleep(2)
        finally:
            for task in self.tasks.values():task.cancel()
            await asyncio.gather(*self.tasks.values(),return_exceptions=True)
            if self.service:await self.service.close()
    async def serve(self,eid):
        while True:
            jobs=set()
            try:
                ticket=await self.register(eid)
                url=transport_url(self.host.cfg['relay'],eid)
                async with connect(url,max_size=140000,open_timeout=15,ping_interval=20) as ws:
                    challenge=json.loads(await asyncio.wait_for(ws.recv(),10))['challenge']
                    if not isinstance(challenge,str) or len(challenge)!=43:raise BotError('relay_challenge')
                    proof=wire.b64u(self.host.sk.sign(('agentjarvis/seat-api-host-v1\n'+eid+'\n'+challenge).encode()))
                    await ws.send(json.dumps({'ticket':ticket,'proof':proof}))
                    if json.loads(await asyncio.wait_for(ws.recv(),10))!={'ready':True}:raise BotError('relay_challenge')
                    async def handle(b):
                        q=b['request']
                        if set(q)!={'endpoint','epoch','method','path','body','key','idem'} or q['endpoint']!=eid or q['epoch']!=self.manager.api_state().endpoint(eid)['epoch']:return
                        if not self.lease_fresh(eid):return
                        status,result=await self.service.dispatch(eid,q['key'],q['method'],q['path'],q['body'],q['idem'])
                        if not self.lease_fresh(eid):return
                        await ws.send(json.dumps({'id':b['id'],'status':status,'body':result}))
                    # Short admission lease. Disconnect loses pending requests, never retries their effects.
                    async with asyncio.timeout(50):
                        async for raw in ws:
                            if not isinstance(raw,str) or len(raw.encode())>70000:raise BotError('relay_frame')
                            b=json.loads(raw)
                            if not isinstance(b,dict) or set(b)!={'id','request'} or len(jobs)>=4:raise BotError('relay_frame')
                            task=asyncio.create_task(handle(b));jobs.add(task);task.add_done_callback(jobs.discard)
            except asyncio.CancelledError:raise
            except BotError as e:
                self.host.st.log('seat_api_transport',endpoint=eid,result=str(e) if str(e)=='endpoint_taken_down' else 'unavailable')
                if str(e)=='endpoint_taken_down':return
                await asyncio.sleep(5)
            except Exception:self.host.st.log('seat_api_transport',endpoint=eid,result='unavailable');await asyncio.sleep(5)
            finally:
                for task in jobs:task.cancel()
                await asyncio.gather(*jobs,return_exceptions=True)
