"""Private metadata-only recovery of bounded temporary tokens; never restores execution authority."""
import copy
import datetime
import hashlib
import hmac
import json
import re
import secrets
import threading
import time

ORIGIN = 'https://api.cloudflare.com'
PATH = '/client/v4/user/tokens/'
MAX_RECORDS = 256


def timestamp(value):
    if not isinstance(value,str):raise ValueError('token_expiry_required')
    result=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('token_expiry_timezone_required')
    return int(result.timestamp())


class Ledger:
    def __init__(self,host):
        self.host=host
        self.path=host.st.root/'bounded-token-recovery.json'
        self.lock=threading.RLock()
        self.key=host.st.signing_key().sign(b'agentj/bounded-token-recovery/commitment/v1')
        try:obj=json.loads(self.path.read_text())
        except FileNotFoundError:obj={'v':1,'records':{}}
        if not isinstance(obj,dict) or obj.get('v')!=1 or not isinstance(obj.get('records'),dict):
            raise ValueError('token_recovery_ledger_invalid')
        self.records=obj['records']

    def identity(self,reference,spec,value):
        # Only the already-authorized named credential is read; this commitment cannot reveal its value.
        public=json.dumps({'ref':reference,'mode':spec.get('mode'),'path':spec.get('path'),'key':spec.get('key')},sort_keys=True,separators=(',',':')).encode()
        return hmac.new(self.key,public+b'\n'+hashlib.sha256(value).digest(),hashlib.sha256).hexdigest()

    def _save(self):
        from .state import _write_private
        _write_private(self.path,json.dumps({'v':1,'records':self.records},sort_keys=True,separators=(',',':')).encode())

    def change(self,rid,**fields):
        with self.lock:
            row=self.records[rid]
            row.update(fields)
            row['updated_at']=int(time.time())
            self._save()
            return copy.deepcopy(row)

    def begin(self,plan,step,reference,spec,value):
        from .approval_review import digest
        expiry=timestamp(step['body']['expires_on'])
        now=int(time.time())
        if not now<expiry<=now+590:raise ValueError('temporary_token_expiry_out_of_bounds')
        origin={"session":plan.session,"task":plan.task}
        with self.lock:
            if len(self.records)>=MAX_RECORDS:raise ValueError('token_recovery_ledger_full')
            rid='tr_'+secrets.token_hex(16)
            self.records[rid]={'id':rid,'host':self.host.channel,'provider':ORIGIN,'token_id':None,
                'requested_expires_at':expiry,'signed_plan_expires_at':int(plan.expires),
                'credential_ref':reference,'credential_identity':self.identity(reference,spec,value),
                'source_handle':digest(origin),
                'original_approval':plan.rid,'created_at':now,'updated_at':now,'state':'creation_unknown',
                'expiry_confirmation':'requested_only','last_approval':plan.rid}
            self._save()
            return rid

    def created(self,rid,identifier,response):
        # Persist the recovery identifier before interpreting optional provider expiry metadata.
        row=self.change(rid,token_id=identifier,state='revocation_unconfirmed')
        reported=response.get('expires_on')
        if reported is not None:
            expiry=timestamp(reported)
            if expiry>row['requested_expires_at'] or expiry<=row['created_at']:
                self.change(rid,state='expiry_unconfirmed')
                raise ValueError('provider_expiry_not_bounded')
            self.change(rid,provider_expires_at=expiry,expiry_confirmation='provider_reported')
        return self.change(rid,state='active_created')

    def get(self,rid):
        with self.lock:
            row=copy.deepcopy(self.records.get(rid))
        if not row or row.get('host')!=self.host.channel or row.get('provider')!=ORIGIN:
            raise ValueError('token_recovery_record_unavailable')
        return row

    def display(self,row,language='zh'):
        expired=time.time()>=row['requested_expires_at']
        state=row['state']
        en=language=='en'
        if state=='revoked_confirmed':label='Revocation confirmed' if en else '已取得撤销回执'
        elif state=='expired_as_requested' or expired:label='Expired as requested; no revocation receipt' if en else '按请求已到期；未取得撤销回执'
        elif state=='active_confirmed':label='Provider reports active; revocation unconfirmed' if en else '服务端核实仍有效；撤销未确认'
        elif not row.get('token_id'):label='Creation result unknown; wait for requested expiry' if en else '创建结果未知；等待请求中的到期时间'
        else:label='Revocation unconfirmed; available for verification' if en else '撤销未确认；可核对'
        return {'record':row['id'],'provider':row['provider'],'token_id':row.get('token_id'),
                'requested_expires_at':row['requested_expires_at'],'signed_plan_expires_at':row['signed_plan_expires_at'],
                'state':'expired_as_requested' if expired and state!='revoked_confirmed' else state,'last_confirmed_state':state,'message':label,'expiry_confirmation':row['expiry_confirmation'],
                'original_approval':row['original_approval'],'source_handle':row['source_handle']}

    def list(self,language='zh'):
        with self.lock:return [self.display(copy.deepcopy(row),language) for row in self.records.values()]

    def prepare(self,raw):
        if set(raw)!={'kind','record','operation'} or raw['operation'] not in ('verify','revoke'):
            raise ValueError('token_recovery_shape')
        row=self.get(raw['record']);operation=raw['operation']
        if (not isinstance(row.get('token_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',row['token_id'])):
            raise ValueError('token_identifier_unknown_wait_for_expiry')
        if operation=='revoke' and row['state'] not in ('active_created','active_confirmed'):
            raise ValueError('verify_token_before_new_revocation')
        if row['state']=='revoked_confirmed':raise ValueError('already_confirmed_revoked')
        return {**raw,'url':ORIGIN+PATH+(row.get('token_id') or ''),
                'credential':row['credential_ref'],'recovery_identity':row['credential_identity'],
                'created_token_id':row.get('token_id'),'requested_expires_at':row['requested_expires_at'],
                'effects':['approval_root'] if operation=='revoke' else ['sensitive_storage'],
                'individual_uv':True}

    def perform(self,step,auth,spec,transport,approval,language='zh'):
        row=self.get(step['record']);operation=step['operation']
        if (row.get('token_id')!=step['created_token_id'] or row['credential_identity']!=step['recovery_identity']
                or row['requested_expires_at']!=step['requested_expires_at']):
            raise ValueError('token_recovery_identity_changed')
        if self.identity(row['credential_ref'],spec,auth)!=row['credential_identity']:
            raise ValueError('recovery_credential_identity_changed')
        if operation=='revoke' and row['state'] not in ('active_created','active_confirmed'):
            raise ValueError('verify_token_before_new_revocation')
        # Record uncertainty before the request. Restart cannot silently repeat this request.
        self.change(row['id'],state='revocation_unknown' if operation=='revoke' else 'verification_unknown',last_approval=approval)
        request={'kind':'token_recovery','url':step['url'],'method':'DELETE' if operation=='revoke' else 'GET',
                 'content_type':'application/json','resolved_ips':step['resolved_ips']}
        status,raw=transport(request,auth,b'{}' if operation=='revoke' else b'')
        obj=json.loads(raw)
        result=obj.get('result') if isinstance(obj,dict) else None
        if (not 200<=status<300 or not isinstance(obj,dict) or obj.get('success') is not True or obj.get('errors')
                or not isinstance(result,dict) or result.get('id')!=row['token_id']):
            raise ValueError('token_recovery_not_confirmed')
        if operation=='revoke':row=self.change(row['id'],state='revoked_confirmed',last_approval=approval)
        else:
            state='active_confirmed' if result.get('status')=='active' else 'revocation_unconfirmed'
            row=self.change(row['id'],state=state,provider_status='active' if state=='active_confirmed' else 'not_confirmed_active',last_approval=approval)
        return self.display(row,language)
