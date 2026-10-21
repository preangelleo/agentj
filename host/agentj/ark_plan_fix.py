"""Confirmed Ark plan mismatch. Keys stay local; a fresh paired signature names one immutable, one-use offer."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
import urllib.error
import urllib.request
from . import ark, elevate, keys, opencode_provider as op, provider_runtime

FILE = 'ark-plan-fix.json'
TTL = 600
PLANS = {'ark-coding-plan': 'coding', 'ark-agent-plan': 'agent'}
ACTION = re.compile(r'ark_fix_([0-9a-f]{24})')


def read_key(name):
    # Exact name only; no shell, no symlinks, no output. A service env assignment wins over an inherited old value.
    value = os.environ.get(name)
    try:
        fd = os.open(provider_runtime.env_path(), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return value
    with os.fdopen(fd, encoding='utf-8') as f:
        mode = os.fstat(f.fileno())
        if mode.st_uid != os.getuid() or mode.st_mode & 0o077 or not stat.S_ISREG(mode.st_mode):
            return None
        for line in f:
            match = keys._LINE.match(line)
            if match and match[1] == name:
                parsed = keys._unquote(match[2].strip())
                if parsed is not None:
                    value = parsed
    return value


def _read(st):
    try:
        data = json.loads((st.root/FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def describe(st, now=None):
    r = _read(st)
    now = time.time() if now is None else now
    if (r.get('used') or type(r.get('at')) not in (int,float) or not 0 <= now-r['at'] < TTL
            or r.get('source') not in PLANS or r.get('target') not in PLANS or r['source']==r['target']
            or not isinstance(r.get('id'),str) or not re.fullmatch(r'[0-9a-f]{24}',r['id'])
            or not isinstance(r.get('model'),str) or not op.MODEL_RE.fullmatch(r['model'])):
        return None
    return {'action':'ark_fix_'+r['id'], 'plan':PLANS[r['target']], 'model':r['target']+'/'+r['model']}


def _models(url, key):
    request = urllib.request.Request(url, headers={'Authorization':'Bearer '+key,'User-Agent':'agentj-ark-plan-check/1'})
    try:
        with urllib.request.build_opener(provider_runtime.NoRedirect).open(request, timeout=8) as r:
            data = json.loads(r.read(65537))
            return 200 if r.status==200 and isinstance(data,dict) and isinstance(data.get('data'),list) else 0
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError):
        return 0


def _native_probe(target, model, source_name, key, *, detail=False):
    # Some plan endpoints do not publish GET /models. Use the owner's OpenCode, isolated and tool-free, never our own chat client.
    from . import harness, agent_opencode2, opencode_free
    exe = harness.agent_bin('opencode')
    if not exe:
        return {"ok":False} if detail else False
    block = json.loads(json.dumps(ark.plan_data(PLANS[target])['block']))
    block['options']['apiKey'] = '{env:'+source_name+'}'
    with tempfile.TemporaryDirectory(prefix='agentj-ark-probe-') as root:
        cfg = Path(root)/'opencode'/'opencode.json'
        cfg.parent.mkdir(mode=0o700)
        cfg.write_text(json.dumps({'provider':{target:block}}))
        env = {k:v for k,v in os.environ.items() if not k.startswith('OPENCODE_CONFIG')}
        env.update(XDG_CONFIG_HOME=root, OPENCODE_CONFIG=str(cfg))
        env[source_name] = key
        try:
            fn = agent_opencode2.probe if harness.opencode_v2(harness.version_of(exe)) else opencode_free.probe
            reply = fn(exe, target+'/'+model, env, 60, waits=())
            ok = isinstance(reply,str) and bool(reply.strip())
            return {"ok":ok} if detail else ok
        except Exception as e:
            from .privacy import redact
            line = e.args[1] if len(e.args)>1 and isinstance(e.args[1],str) else None
            line = redact(line.replace(key,'[redacted]')).splitlines()[0][:200] if line else None
            return {"ok":False, **({"line":line} if line else {})} if detail else False


def diagnose(st, model, http_status=401, *, force=False, check=None, native=None):
    cfg=st.agent_config() or {}
    if cfg.get('kind')=='opencode' and cfg.get('shared_opencode_port'):
        return None
    if http_status!=401 or not isinstance(model,str) or '/' not in model:
        return None
    source, mid = model.split('/',1)
    if source not in PLANS:
        return None
    target = 'ark-agent-plan' if source=='ark-coding-plan' else 'ark-coding-plan'
    target_plan = PLANS[target]
    try:
        mid = ark._model(mid,target_plan)
        src = provider_runtime.configured().get(source,{})
        expected = ark.plan_data(PLANS[source])['block']['options']['baseURL']
        if provider_runtime.options(src).get('baseURL')!=expected:
            return None
        name = provider_runtime.key_name(source)
        if not name:
            return None
        value = read_key(name)
        if not value:
            return None
        digest = hashlib.sha256(value.encode()).hexdigest()
        old = _read(st)
        view = describe(st)
        if view and all(old.get(k)==v for k,v in {'source':source,'target':target,'model':mid,'key_hash':digest,'force':bool(force)}.items()):
            return view
        status = (check or _models)(ark.plan_data(target_plan)['block']['options']['baseURL'].rstrip('/')+'/models',value)
        if status!=200 and not (status in (404,405) and (native or _native_probe)(target,mid,name,value)):
            return None
        digest = hashlib.sha256(value.encode()).hexdigest()
        with st.config_lock():
            old = _read(st)
            view = describe(st)
            if view and all(old.get(k)==v for k,v in {'source':source,'target':target,'model':mid,'key_hash':digest,'force':bool(force)}.items()):
                return view
            rec={'id':secrets.token_hex(12),'at':time.time(),'source':source,'target':target,'model':mid,'key_name':name,
                 'key_hash':digest,'force':bool(force),'used':False}
            st.write_private(st.root/FILE,json.dumps(rec).encode())
        return describe(st)
    except (OSError, ValueError, op.ProviderError):
        return None


async def notify(host, model):
    if getattr(host, '_ark_plan_probe', False):
        return
    host._ark_plan_probe=True
    try:
        offer = await asyncio.to_thread(diagnose,host.st,model)
        if offer:
            await host._send_p33(lambda s:{'t':'ark_plan',**view(host.st)})
        else:
            await provider_runtime.request_key(host, model.split('/',1)[0])
    finally:
        host._ark_plan_probe=False


def apply(st, action, *, probe=None, request=None):
    from . import switch_harness
    match = ACTION.fullmatch(action) if isinstance(action,str) else None
    if not match:
        return {'ok':False,'why':'shape'}
    try:
        with st.config_lock():
            r = _read(st)
            view = describe(st)
            if not view or view['action']!=action:
                return {'ok':False,'why':'stale'}
            name = r.get('key_name')
            if not isinstance(name,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,63}',name) or name!=provider_runtime.key_name(r['source']):
                return {'ok':False,'why':'changed'}
            value = read_key(name)
            if not value or not isinstance(r.get('key_hash'),str) or not hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(),r['key_hash']):
                return {'ok':False,'why':'changed'}
            target_plan = PLANS[r['target']]
            dest = ark.plan_data(target_plan)['key_env']
            previous = read_key(dest)
            if previous and not hmac.compare_digest(previous,value):
                return {'ok':False,'why':'target_key_conflict'}
            target_cfg = provider_runtime.configured().get(r['target'])
            if target_cfg and (provider_runtime.options(target_cfg).get('baseURL')!=ark.plan_data(target_plan)['block']['options']['baseURL']
                    or provider_runtime.key_name(r['target'])!=dest):
                return {'ok':False,'why':'target_provider_conflict'}
            r['used']=True
            st.write_private(st.root/FILE,json.dumps(r).encode())
            if not previous:
                spec=elevate.parse_dest('env:'+provider_runtime.env_path()+'#'+dest,dest,None,st.root)
                buf=bytearray(value.encode())
                try: elevate.write_secret(spec,buf)
                finally: elevate.wipe(buf)
            inst=ark.install(r['model'],plan=target_plan)
        # The key/provider intentionally remain available after a failed probe; the main route does not change on failure.
        cfg=st.agent_config() or {}
        if cfg.get('kind')=='opencode':
            tested=(probe(inst['model']) if probe else _native_probe(r['target'], r['model'], dest, value, detail=True))
            if not tested.get('ok'):
                line=tested.get('line')
                if isinstance(line,str):
                    from .privacy import redact
                    line=redact(line.replace(value,'[redacted]')).splitlines()[0][:200] if line else None
                return {'ok':False,'why':'probe_failed',**({'line':line} if line else {})}
            st.set_agent_model(inst['model'])
            return {'ok':True,'model':inst['model'],'plan':target_plan}
        previous_job=(switch_harness.read_receipt(st) or {}).get('job')
        res=(request or switch_harness.request)(st,'opencode',inst['model'],force=r.get('force') is True)
        pending=bool(res.get('pending') or res.get('scheduled'))
        rec=switch_harness.read_receipt(st) or {}
        job=res.get('job') or (rec.get('job') if pending and rec.get('job')!=previous_job and rec.get('target')=='opencode' and rec.get('model')==inst['model'] else None)
        if isinstance(job,str):
            with st.config_lock():
                r['job']=job
                st.write_private(st.root/FILE,json.dumps(r).encode())
        return {**res,'model':inst['model'],'plan':target_plan,**({'pending':True,'ok':True} if pending else {})}
    except (OSError, ValueError, elevate.Refused, op.ProviderError):
        return {'ok':False,'why':'write_failed'}


def view(st):
    from . import switch_harness
    cfg=st.agent_config() or {}
    model=cfg.get('model')
    current=model if cfg.get('kind')=='opencode' and isinstance(model,str) and model.split('/',1)[0] in PLANS else None
    rec=_read(st)
    result=switch_harness.read_receipt(st) or {}
    outcome=result.get('status') if rec.get('job') and result.get('job')==rec['job'] and result.get('status') in ('ok','failed','rolled_back') else None
    return {'offer':describe(st),'current':current,'outcome':outcome}


def checking_note(model, lang, data):
    from .privacy import redact
    from .text import clean_line
    text = ('方舟拒绝了这把 key，正在核对套餐。' if lang.startswith('zh') else 'Ark rejected this key; checking which plan it belongs to.')
    line = data.get('message') if isinstance(data,dict) else None
    if isinstance(line,str) and line:
        try:
            name = provider_runtime.key_name(model.split('/',1)[0])
            key = read_key(name) if name else None
        except (OSError,ValueError):
            key = None
        line = line.replace(key,'[redacted]') if key else line
        text += '\n' + clean_line(redact(line).splitlines()[0],200)
    return text


def guidance(fix, lang='zh'):
    if not isinstance(fix,dict) or fix.get('plan') not in ('agent','coding'):
        return ''
    plan='Agent Plan' if fix['plan']=='agent' else 'Coding Plan'
    return (f'This key belongs to {plan}. On the phone, open Emergency repair and tap Use {plan} and retry; no key paste needed.' if lang=='en' else
            f'这把 key 属于 {plan}。在手机「紧急修复」点「改用 {plan} 并重试」，不用重贴 key。')
