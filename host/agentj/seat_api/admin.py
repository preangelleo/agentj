"""ApiAdmin: owner-signed management of the seat API (ADR-A193 §7). Called only from the paired owner channel.

The main Agent may propose (`agentj api request`), never authorize. Secrets leave only through the Face ID key card.
"""
import asyncio
import secrets
import copy
import hashlib
from ..bots.store import BotError,canonical,ident
from ..compose import Send

READS={'api_list','api_usage','api_spec','api_review','api_manifest','api_workflows'}
WRITES={'api_create','api_switch','api_publish','api_issue','api_revoke','api_version_publish','api_key_rebind','api_spec_publish',
        'api_provisioner_issue','api_provisioner_revoke','api_callback_save','api_callback_delete',
        'api_migrate_prepare','api_migrate_export','api_migrate_import','api_migrate_abort','api_handoff'}

FIELDS={
 'api_create':{'name','bot','privacy','rest_accepted','workflow'},'api_switch':{'enabled'},
 'api_publish':{'id','enabled'},'api_issue':{'id','label','days','limits'},'api_revoke':{'key_id'},
 'api_version_publish':{'id','reviewed'},'api_key_rebind':{'key_id'},'api_spec_publish':{'id','public'},
 'api_provisioner_issue':{'id','label','max_keys','max_days','days','usage'},
 'api_provisioner_revoke':{'provisioner'},'api_callback_save':{'id','kind','url','recipient'},
 'api_callback_delete':{'callback'},'api_migrate_prepare':set(),
 'api_migrate_export':{'id','target_host_pk','import_pk'},'api_migrate_import':{'import_id','bundle','source_pk'},
 'api_migrate_abort':{'id'},'api_handoff':{'id','purpose'}}

def validate_request(req):
    from .. import privacy
    from .workflow import _strings
    if (not isinstance(req,dict) or req.get('op') not in FIELDS
            or set(req)-({'op'}|FIELDS[req['op']]) or len(canonical(req).encode())>2*1024*1024):
        raise BotError('invalid_request')
    # API management accepts references/public metadata only, never raw credentials.
    if any(pattern.search(text) for text in _strings(req) for _,pattern,_ in privacy._SECRETS):
        raise BotError('config_contains_secret')

class ApiHandoffSend(Send):
    """Metadata-only owner handoff to the main Agent: public address, contract version, pin and key references."""
    def __init__(self,hid,text):super().__init__('api-handoff',hid,0,text=text,by='Agent J API handoff')
    async def wait_ready(self):return True

class ApiAdmin:
    def __init__(self,manager):self.manager=manager;self.host=manager.host
    def api(self):return self.manager.api_state()
    def runtime(self):
        if not self.manager.api_runtime:raise BotError('registration_unavailable')
        return self.manager.api_runtime
    def account(self,eid):
        rt=self.manager.api_runtime;return rt.account(eid) if rt else None
    def read(self,req):
        op=req.get('op');api=self.api();eid=req.get('id')
        if op=='api_list':return {**api.metadata(),'registrations':self.manager.api_runtime.receipts if self.manager.api_runtime else {}}
        if op=='api_usage':
            out=api.usage(ident(eid) if eid else None,req.get('since',0),req.get('limit',1000),req.get('format','json'))
            out['requests']=[dict(r) for r in api.db.execute('SELECT id,endpoint,key_id,version,method,created,expires,status FROM api_requests ORDER BY created DESC LIMIT 200')]
            out['deliveries']=[dict(r) for r in api.db.execute('SELECT id,callback,event_id,attempts,status,created FROM api_deliveries ORDER BY created DESC LIMIT 200')]
            return out
        if op=='api_spec':
            from .spec import generate
            account=self.account(eid)
            if not account:raise BotError('registration_unavailable')
            return {'spec':generate(api.endpoint(eid),account,api)}
        if op=='api_review':return api.review(eid)
        if op=='api_manifest':
            from . import e2e
            e=api.endpoint(eid);return e2e.manifest(api.secret(eid),e,api.now())
        if op=='api_workflows':
            from . import workflow
            root=api.workflow_root;names=sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
            return {'sandbox':workflow.qualify(),'workflows':names}
        raise BotError('unknown_operation')
    def owner(self):
        from .. import provenance
        session=getattr(self,'_session',None)
        if (session is None or provenance._paired_source()!=provenance.phone()
                or not self.host._paired_live(session) or self.host.stopped()
                or (getattr(self,'_gone',None) is not None and self._gone.done())
                or self.runtime().local_binding()!=self._binding):
            raise BotError('signed_owner_required')
        return session
    def plan(self,req):
        api=self.api()
        return {'request':req,'binding':self.runtime().local_binding(),
                'endpoints':api.metadata(),
                'contracts':{e['id']:api.current(e) for e in api.metadata()['endpoints']}}
    async def write(self,req,*,session=None,gone=None):
        self._session=session;self._gone=gone
        self._binding=self.runtime().local_binding()
        self.owner()
        validate_request(req)
        req=copy.deepcopy(req)
        before=hashlib.sha256(canonical(self.plan(req)).encode()).hexdigest()
        preview={k:({'sha256':hashlib.sha256(canonical(v).encode()).hexdigest()} if k=='bundle' else v)
                 for k,v in req.items() if k not in ('op','id')}
        answer=await self.host.ask('agentj api manage',
            {'operation':req['op'],'endpoint':req.get('id'),'changes':preview, 'request_sha256':hashlib.sha256(canonical(req).encode()).hexdigest(),
             'target_sha256':before},gone=gone,batch=False,grant_info=True,only=session.device)
        self.owner()
        proof=answer.get('grant_info') or {}
        if (answer.get('behavior')!='allow' or proof.get('uv') is not True
                or proof.get('device')!=session.device or not proof.get('sign_pub')
                or self.host.st.sign_key(session.device)!=proof['sign_pub']
                or hashlib.sha256(canonical(self.plan(req)).encode()).hexdigest()!=before):
            raise BotError('owner_uv_required')
        return await self._write(req)
    async def card(self,name,purpose,value):
        session=self.owner()
        card=await self.host.elevate.out.send({'t':'secret_out','name':name,'purpose':purpose,'kind':'text','value':value,'only':session.device})
        self.owner()
        if card.get('result')!='sent':raise BotError('key_card_unavailable')
        return card['id']
    async def _write(self,req):
        if self.host.stopped():raise BotError('offline')
        api=self.api();op=req.get('op');eid=req.get('id')
        if op=='api_switch':api.switch(req.get('enabled'));return {'enabled':api.enabled()}
        if op=='api_create':return {'endpoint':api.create(req.get('name'),req.get('bot'),req.get('privacy','e2e'),req.get('rest_accepted',False),req.get('workflow'))}
        if op=='api_publish':
            enabled=req.get('enabled');api.publish(eid,enabled)
            try:
                await self.runtime().register(eid)
                self.owner()
            except BaseException:
                api.publish(eid,False);raise
            return {'endpoint':api.endpoint(eid)}
        if op=='api_revoke':api.revoke(req.get('key_id'));return {'revoked':True}
        if op=='api_version_publish':return {'endpoint':api.publish_version(eid,req.get('reviewed'))}
        if op=='api_key_rebind':return {'version':api.rebind(req.get('key_id'))}
        if op=='api_spec_publish':api.publish_spec(eid,req.get('public'));return {'endpoint':api.endpoint(eid)}
        if op=='api_issue':
            issued=api.issue(eid,req.get('label'),req.get('days',30),req.get('limits'))
            try:return {'key_id':issued['id'],'expires_at':issued['expires_at'],'card_id':await self.card('API '+issued['id'][:8],'Caller key; keep on your customer backend / 客户调用 key，只放客户后端',issued['secret'])}
            except BaseException:
                api.revoke(issued['id']);raise
            finally:issued.pop('secret',None)
        if op=='api_provisioner_issue':
            issued=api.provisioner(eid,req.get('label'),req.get('max_keys',10),req.get('max_days',30),req.get('days',30),req.get('usage',False))
            try:return {'provisioner_id':issued['id'],'expires_at':issued['expires_at'],'card_id':await self.card('API provisioner '+issued['id'][:8],'Key-issuing capability for your own website backend only / 只给你自己网站后端的发 key 能力',issued['secret'])}
            except BaseException:
                api.revoke_provisioner(issued['id']);raise
            finally:issued.pop('secret',None)
        if op=='api_provisioner_revoke':api.revoke_provisioner(req.get('provisioner'));return {'revoked':True}
        if op=='api_callback_save':
            saved=api.save_callback(eid,req.get('kind'),req.get('url'),req.get('recipient'))
            try:return {'callback_id':saved['id'],'card_id':await self.card('API callback '+saved['id'][:8],'Callback signing secret; verify X-AgentJ-Signature / 回调签名密钥',saved['secret'])}
            except BaseException:
                api.delete_callback(saved['id']);raise
            finally:saved.pop('secret',None)
        if op=='api_callback_delete':api.delete_callback(req.get('callback'));return {'deleted':True}
        if op=='api_migrate_prepare':
            from ..wire import b64u
            from cryptography.hazmat.primitives import serialization
            pk=self.host.sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
            return {**api.import_key(),'host_pk':b64u(pk)}
        if op=='api_migrate_export':
            target=req.get('target_host_pk')
            if not isinstance(target,str) or not isinstance(req.get('import_pk'),str):raise BotError('invalid_request')
            api.begin_migration(eid)
            try:
                # Pending jobs finish on this host first; new admissions are refused meanwhile.
                for _ in range(600):
                    if not api.db.execute("SELECT count(*) FROM api_requests WHERE endpoint=? AND status IN ('queued','running')",(eid,)).fetchone()[0]:break
                    await asyncio.sleep(1)
                    self.owner()
                bundle=api.export(eid,req['import_pk'],self.host.sk)
                await self.runtime().migrate(eid,bundle['from_epoch'],target)
                self.owner()
            except BaseException:
                api.abort_migration(eid);raise
            api.finish_export(eid)
            from ..wire import b64u
            from cryptography.hazmat.primitives import serialization
            return {'bundle':bundle,'source_pk':b64u(self.host.sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw))}
        if op=='api_migrate_import':
            result=api.import_bundle(req.get('import_id'),req.get('bundle'),req.get('source_pk'))
            return {'endpoint':result['endpoint'],'contract_matches':result['contract_matches']}
        if op=='api_migrate_abort':
            api.abort_migration(eid)
            from ..wire import b64u
            from cryptography.hazmat.primitives import serialization
            e=api.endpoint(eid)
            # Clears any pending D1 handover (SEAT_API.md §5: migrate to the caller itself = abort).
            await self.runtime().migrate(eid,e['epoch'],b64u(self.host.sk.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)))
            self.owner()
            return {'endpoint':e}
        if op=='api_handoff':return await self.handoff(eid,req.get('purpose'))
        raise BotError('unknown_operation')
    async def handoff(self,eid,purpose):
        api=self.api();e=api.endpoint(eid);account=self.account(eid)
        if not account or not e['enabled']:raise BotError('registration_unavailable')
        if not isinstance(purpose,str) or not 1<=len(purpose)<=200 or any(ord(c)<32 for c in purpose):raise BotError('invalid_request')
        from .. import privacy
        if any(p.search(purpose) for _,p,_ in privacy._SECRETS):raise BotError('invalid_request')
        pub=api.public_keys(eid)
        keys=[{'key_id':k['id'],'label':k['label'],'expires':k['expires']} for k in api.db.execute('SELECT id,label,expires FROM api_keys WHERE endpoint=? AND revoked=0 AND version=?',(eid,e['version']))]
        meta={'url':'https://api.agentj.app/v1/'+account+'/'+e['public_id'],'contract_version':e['version'],'privacy':e['privacy'],'backend':'workflow' if e['workflow'] else 'bot',
              'pin':pub['pin'],'openapi':'agentj api spec '+e['id'],'keys':keys,'purpose':purpose}
        hid=secrets.token_hex(16)
        text=('Owner-approved seat API handoff (metadata only, no secrets): '+canonical(meta)+
              '\nUse agentj api list/spec and api request --file (signed owner proposals only). Caller keys are never in chat: reference them as $AGENTJ_API_KEY from the owner backend secret store. '
              'Domains, payments and deployment need the owner\'s separate approval.')
        agent=self.host.agent
        if not agent or self.host.stopped():raise BotError('agent_unavailable')
        send=ApiHandoffSend(hid,text);agent.submit(send)
        # "Sent" is only shown after the main Agent took the message.
        for _ in range(100):
            if send.state in ('delivered','failed','uncertain','cancelled'):break
            await asyncio.sleep(0.1)
            self.owner()
        return {'handoff':hid,'delivered':send.state=='delivered','state':send.state}
