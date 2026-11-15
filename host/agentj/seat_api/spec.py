"""OpenAPI 3.1 generated from the endpoint contract. No prompt, tools, paths, knowledge or credentials.

The same document serves the owner's private export and, only when the owner made it public, `GET …/openapi.json`.
"""
ERRORS=[(400,'invalid_envelope','Malformed E2E envelope / E2E 信封格式错误'),(401,'key_invalid','Invalid, expired or revoked key / key 无效、过期或已吊销'),
        (403,'key_scope','Key bound to another endpoint or contract version; e2e_required on E2E-only endpoints / key 范围或版本不符'),
        (404,'not_found','Unknown, paused or not yours / 未知、暂停或不属于此 key'),(409,'idempotency_conflict','Same Idempotency-Key, different body; contract_changed; replay / 幂等冲突'),
        (410,'request_expired','Result past 24 h retention, deleted, or endpoint retired / 结果过期或端点下线'),(413,'too_large','Body over 64 KiB / 超过 64 KiB'),
        (422,'invalid_input','Input or schema mismatch / 输入或 schema 不符'),(429,'rate_limit','Rate, token budget, queue full (retry_after) / 频率、预算或队列已满'),
        (503,'offline','Host offline, queue timeout or safety review unavailable; no cloud queue (Retry-After: 30) / 主机离线、排队超时或审核不可用，不云排队'),
        (504,'deadline','Synchronous deadline (30 s); check the request before retrying / 同步超时，先查原请求再重试')]
def _errors():return {str(c):{'description':d+' (`'+k+'`)','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}} for c,k,d in ERRORS}
IDEM={'name':'Idempotency-Key','in':'header','required':True,'schema':{'type':'string','maxLength':64}}
def generate(endpoint,account,store=None):
    url='https://api.agentj.app/v1/'+account+'/'+endpoint['public_id'];wf=endpoint.get('workflow')
    if wf and store is not None:
        from . import workflow
        d=workflow.load(store.workflow_root,wf);inp,out=d['input_schema'],d['output_schema']
    else:
        inp={'type':'object','properties':{'message':{'type':'string','maxLength':2000}},'required':['message'],'additionalProperties':False}
        out={'type':'object','properties':{'message':{'type':'string'}},'required':['message'],'additionalProperties':False}
    request={'type':'object','properties':{'input':inp,**({} if wf else {'conversation_id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,64}$'}})},'required':['input'],'additionalProperties':False}
    job_request={**request,'properties':{**request['properties'],'callback_id':{'type':'string','description':'Owner-registered callback / 主人登记的回调'}}}
    usage={'type':'object','properties':{k:{'type':['integer','null']} for k in ('total_tokens','input_tokens','output_tokens')},'description':'Unknown provider usage is null, never 0 / 未知用量为 null'}
    result={'type':'object','properties':{'request_id':{'type':'string'},'output':out,'usage':usage,'contract_version':{'type':'integer'}},'required':['request_id','output','usage','contract_version']}
    sec=[{'callerKey':[]}]
    paths={'/':{'post':{'summary':'Synchronous call (30 s) / 同步调用','security':sec,'parameters':[IDEM],'requestBody':{'required':True,'content':{'application/json':{'schema':request}}},
                        'responses':{'200':{'description':'Reviewed result / 已审核结果','content':{'application/json':{'schema':result}}},**_errors()}}},
           '/jobs':{'post':{'summary':'Asynchronous job (202 only after the host persisted it; 10 min) / 异步任务','security':sec,'parameters':[IDEM],
                            'requestBody':{'required':True,'content':{'application/json':{'schema':job_request}}},
                            'responses':{'202':{'description':'Accepted on the host / 主机已受理','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Job'}}}},**_errors()}}},
           '/jobs/{job_id}':{'parameters':[{'name':'job_id','in':'path','required':True,'schema':{'type':'string','pattern':'^[a-f0-9]{32}$'}}],
                             'get':{'summary':'Job status and reviewed result / 任务状态','security':sec,'responses':{'200':{'description':'Status','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Job'}}}},**_errors()}},
                             'delete':{'summary':'Cancel and delete; side effects already done are not undone / 取消并删除','security':sec,'responses':{'200':{'description':'Deleted'},**_errors()}}},
           '/_e2e':{'get':{'summary':'Signed manifest; verify against the pin you received out of band / 签名清单，按带外 pin 校验','security':[],'responses':{'200':{'description':'Manifest'}}},
                    'post':{'summary':'seat-api-v1 envelope (use the official SDK) / E2E 信封','security':[],'responses':{'200':{'description':'Encrypted response'},**_errors()}}}}
    if not wf:
        paths['/chat/completions']={'post':{'summary':'OpenAI-compatible non-streaming subset; model = endpoint id / OpenAI 兼容非流式子集','security':sec,'parameters':[IDEM],
            'requestBody':{'required':True,'content':{'application/json':{'schema':{'type':'object','properties':{'model':{'const':endpoint['public_id']},'stream':{'const':False},
                'messages':{'type':'array','maxItems':20,'items':{'type':'object','properties':{'role':{'enum':['user','assistant']},'content':{'type':'string'}},'required':['role','content'],'additionalProperties':False}}},
                'required':['model','messages'],'additionalProperties':False}}}},'responses':{'200':{'description':'chat.completion'},**_errors()}}}
    if endpoint['privacy']=='e2e':
        for p,item in paths.items():
            if p!='/_e2e':
                for op in item.values():
                    if isinstance(op,dict) and 'summary' in op:op['description']='E2E-only endpoint: send this request inside the /_e2e envelope; plaintext gets 403 e2e_required. / 仅 E2E：放入 /_e2e 信封。'
    curl=('curl -H "Authorization: Bearer $AGENTJ_API_KEY" -H "Idempotency-Key: unique-call-id" -H "Content-Type: application/json" -d \''
          +('{"input":{}}' if wf else '{"model":"'+endpoint['public_id']+'","messages":[{"role":"user","content":"Hello"}]}')+"' "+url+('' if wf else '/chat/completions'))
    return {'openapi':'3.1.0','info':{'title':endpoint['public_id'],'version':str(endpoint['version']),
            'description':'Agent J seat API. Host must be online (no cloud queue). Default key limits: 10/min, 100/day, 100k tokens/day, 30 days. '
                          '/ 主机需在线；默认每 key 10 次/分钟、100 次/天、10 万 tokens/天、30 天到期。'},
            'servers':[{'url':url}],'paths':paths,
            'components':{'securitySchemes':{'callerKey':{'type':'http','scheme':'bearer','description':'Keep on your backend only / 只放客户后端'}},
                          'schemas':{'Error':{'type':'object','properties':{'error':{'type':'object','properties':{'code':{'type':'string'}}},'request_id':{'type':'string'}}},
                                     'Job':{'type':'object','properties':{'job_id':{'type':'string'},'status':{'enum':['queued','running','succeeded','failed','cancelled','expired','deleted']},'expires_at':{'type':'integer'},'result':{'type':['object','null']}}}}},
            'x-agentj-privacy':endpoint['privacy'],'x-agentj-backend':'workflow' if wf else 'bot',
            'x-agentj-supported':{'openai_chat':not wf,'streaming':False,'jobs':True,'e2e_sdk':True,'webhook_receive':False},
            'x-agentj-curl':curl}
