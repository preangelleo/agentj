"""Resident visitor transport: separate /b/ socket, metadata leases, host-only model.

Never feeds visitor plaintext into the owner's decoder, harness or approvals. Owner
write-tool cards are constructed from registered definitions by the host, not visitor frames.
"""
import asyncio
import contextlib
import hashlib
import json
import secrets
import struct
import time
from websockets.asyncio.client import connect
from .. import cloud,wire
from .store import BotError,canonical
from .visitor import VisitorSession,public
from .engine import Engine,UNAVAILABLE

ISSUER=wire.unb64u('XwR9RvhcZ3uFFDthatG1_eDojDdyYPyDHIWwxFOeeTQ')

class Runtime:
    def __init__(self,manager):
        self.manager=manager;self.host=manager.host;self.tasks={};self.active={};self.stopping=False;self.epochs={};self.synced={};self.pruned=0;self.telegram_tasks={};self.telegram_epochs={};self.telegram_targets={}
    def engine(self):
        agent=self.host.agent
        return Engine(self.manager.state(),getattr(agent,'kind',None) or (self.host.agent_cfg or {}).get('kind'),agent.cur_model() if agent and hasattr(agent,'cur_model') else None,approve=self.manager.approve,handoff=self.manager.handoff)
    async def run(self):
        try:
            while not self.stopping:
                # No bot disk state and no bound seat: don't create a database or call an external service.
                configured=self.host.st.root/'bots'/'bots.sqlite3'
                bots=self.manager.state().listing(include_removed=True) if configured.exists() else []
                if configured.exists() and time.monotonic()-self.pruned>=3600:self.manager.state().prune();self.pruned=time.monotonic()
                wanted={b['id'] for b in bots if b['enabled'] and not self.manager.state().lifecycle(b['id'])['removed']} if cloud.read_cloud(self.host.st) and not self.host.stopped() else set()
                for bid in list(self.tasks):
                    if bid not in wanted or self.tasks[bid].done() or self.epochs.get(bid)!=self.manager.state().lifecycle(bid)['epoch']:
                        self.tasks[bid].cancel();await asyncio.gather(self.tasks.pop(bid),return_exceptions=True)
                telegram_wanted={b['id'] for b in bots if b['enabled'] and b['telegram']['enabled'] and not self.manager.state().lifecycle(b['id'])['removed']} if cloud.read_cloud(self.host.st) and not self.host.stopped() else set()
                for bid in list(self.telegram_tasks):
                    if bid not in telegram_wanted or self.telegram_tasks[bid].done() or self.telegram_epochs.get(bid)!=self.manager.state().lifecycle(bid)['epoch']:
                        self.telegram_tasks[bid].cancel();await asyncio.gather(self.telegram_tasks.pop(bid),return_exceptions=True)
                        for key in list(self.telegram_targets):
                            if key[0]==bid:self.telegram_targets.pop(key,None)
                for bid in telegram_wanted:
                    if bid not in self.telegram_tasks:
                        from .telegram import Channel
                        try:channel=Channel(self,bid)
                        except BotError:continue
                        self.telegram_epochs[bid]=channel.epoch;self.telegram_tasks[bid]=asyncio.create_task(channel.run())
                if cloud.read_cloud(self.host.st):
                    for b in bots:
                        bid=b['id'];epoch=self.manager.state().lifecycle(bid)['epoch']
                        if self.synced.get(bid,(0,0))[0]!=epoch or time.monotonic()-self.synced.get(bid,(0,0))[1]>=60:
                            try:await self.register(bid);self.synced[bid]=(epoch,time.monotonic())
                            except Exception:self.host.st.log('bots',outcome='registration_unavailable')
                for bid in wanted:
                    if bid not in self.tasks:
                        self.epochs[bid]=self.manager.state().lifecycle(bid)['epoch'];self.tasks[bid]=asyncio.create_task(self.serve_bot(bid))
                await asyncio.sleep(2)
        finally:
            for task in self.telegram_tasks.values():task.cancel()
            await asyncio.gather(*self.telegram_tasks.values(),return_exceptions=True);self.telegram_tasks.clear();self.telegram_targets.clear()
            for task in self.tasks.values():task.cancel()
            await asyncio.gather(*self.tasks.values(),return_exceptions=True);self.tasks.clear()
    async def register(self,bid):
        store=self.manager.state();c=store.get(bid);now=int(time.time());life=store.lifecycle(bid);epoch=life['epoch']
        manifest={'bot':bid,'host':wire.b64u(public(self.host.sk)),'epoch':epoch,'expires':now+300,'slug':c['slug'],'title':c['title'],
                  'description':c['description'],'language':c['language'],'enabled':c['enabled'],'status':'removed' if life['removed'] else 'enabled' if c['enabled'] else 'paused','embed_origins':c['embed_origins']}
        signature=wire.b64u(self.host.sk.sign(b'agentjarvis/bot-manifest-v1\n'+canonical(manifest).encode()))
        inner={'v':1,'t':'bots_register','channel':self.host.channel,'ts':now,'seq':time.time_ns()//1000000,'manifest':manifest,'manifest_sig':signature}
        code,value=await asyncio.to_thread(cloud.post_json,cloud.api_url(self.host.st)+'/v1/host/bots/register',cloud.envelope('agentjarvis-host-bots-register-v1',inner,self.host.sk))
        if code!=200 or not isinstance(value.get('host_ticket'),dict):raise BotError('registration_unavailable')
        return value['host_ticket'],epoch
    async def serve_bot(self,bid):
        while not self.stopping:
            try:
                ticket,epoch=await self.register(bid)
                url=self.host.cfg['relay'].rstrip('/')+'/b/'+bid+'/h'
                async with connect(url,max_size=16405,ping_interval=20,open_timeout=15) as ws:
                    challenge=json.loads(await asyncio.wait_for(ws.recv(),10))['challenge']
                    if not isinstance(challenge,str) or len(challenge)>64:raise BotError('relay_challenge')
                    proof=wire.b64u(self.host.sk.sign(('agentjarvis/bot-relay-host-v1\n'+bid+'\n'+challenge).encode()))
                    await ws.send(json.dumps({'ticket':ticket,'proof':proof}))
                    sessions={};jobs=set();send_lock=asyncio.Lock()
                    async def send(cid,data):
                        async with send_lock:await ws.send(bytes([1])+struct.pack('>I',cid)+data)
                    async def disconnect(cid):
                        async with send_lock:await ws.send(bytes([2])+struct.pack('>I',cid))
                    async def refresh_lease():
                        while True:await asyncio.sleep(60);await self.register(bid)
                    lease=asyncio.create_task(refresh_lease())
                    async def handle(cid,session,cap,obj):
                        try:
                            store=self.manager.state();store.authenticate(bid,session.visitor,cap)
                            if obj['t']=='say':result=await self.engine().reply(bid,session.visitor,cap,obj['r'],obj['text'])
                            elif obj['t']=='identity':store.set_identity(bid,session.visitor,obj['identity']);result={'status':'identity_saved'}
                            else:await self.manager.handoff(bid,session.visitor,obj['r']);result={'status':'human_requested','text':'A human has been notified. / 已通知人工。'}
                        except Exception:result={'status':'refused','text':UNAVAILABLE}
                        with contextlib.suppress(Exception):await send(cid,session.cipher.seal({'t':'reply','r':obj.get('r','') if 'obj' in locals() else '',**result}))
                    try:
                        # Host relay ticket has finite TTL. Reconnect uses a new proof and clears session keys.
                        async with asyncio.timeout(240):
                            async for raw in ws:
                                if not isinstance(raw,bytes) or len(raw)<5:raise BotError('relay_frame')
                                op,cid=raw[0],struct.unpack('>I',raw[1:5])[0]
                                if op==0:
                                    if len(sessions)>=16:await disconnect(cid);continue
                                    session=VisitorSession(bid,epoch,self.host.sk,ISSUER)
                                    try:answer=session.accept(json.loads(raw[5:]))
                                    except Exception:await disconnect(cid);continue
                                    vid,cap=self.manager.state().admit_visitor(bid,session.visitor);session.visitor=vid
                                    sessions[cid]=(session,cap);self.active[(bid,vid)]=(cid,session,send)
                                    await send(cid,canonical(answer).encode())
                                elif op==1 and cid in sessions:
                                    session,cap=sessions[cid]
                                    try:obj=session.decode(raw[5:])
                                    except BotError:
                                        self.active.pop((bid,session.visitor),None);sessions.pop(cid,None);await disconnect(cid);continue
                                    if len(jobs)>=20:
                                        await send(cid,session.cipher.seal({'t':'reply','r':obj['r'],'status':'refused','text':UNAVAILABLE}));continue
                                    task=asyncio.create_task(handle(cid,session,cap,obj));jobs.add(task);task.add_done_callback(jobs.discard)
                                elif op==2 and cid in sessions:
                                    session,_=sessions.pop(cid);self.active.pop((bid,session.visitor),None)
                                else:raise BotError('relay_frame')
                    finally:
                        lease.cancel();await asyncio.gather(lease,return_exceptions=True)
                        for task in jobs:task.cancel()
                        await asyncio.gather(*jobs,return_exceptions=True)
                        for session,_ in sessions.values():self.active.pop((bid,session.visitor),None)
            except asyncio.CancelledError:raise
            except Exception:
                self.host.st.log('bots_transport',bot=bid,result='unavailable')
                await asyncio.sleep(10)
    async def deliver(self,bid,vid,rid,result):
        target=self.active.get((bid,vid))
        if not target:
            telegram=self.telegram_targets.get((bid,vid))
            if not telegram:return False
            channel,chat=telegram
            if not channel.enabled():return False
            await channel.deliver(chat,result)
            return True
        cid,session,send=target
        await send(cid,session.cipher.seal({'t':'reply','r':rid,**result}));return True
