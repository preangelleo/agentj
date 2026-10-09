"""Host-local provider profiles; keys are read only by code, never CLI arguments/output.

The paired phone writes a private key file. A switch verifies a real model request
before merging native configuration; a guarded restore preserves concurrent edits.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import ssl
import socket
import tempfile
import time
import urllib.error
import urllib.request

from .opencode_provider import ID_RE, MODEL_RE, ProviderError, _check_url, block, config_file

PRESETS = {'openai': 'https://api.openai.com/v1', 'anthropic': 'https://api.anthropic.com/v1',
           'agentsrelay': 'https://agentsrelay.net/v1'}


def root():
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'agentj/provider-profiles'


def private_dir(p):
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    if p.is_symlink() or not p.is_dir() or p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077:
        raise ProviderError('unsafe_profile_directory')


def secure_read(p, limit=2*1024*1024):
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ProviderError('unsafe_private_file')
        data = f.read(limit+1)
        if len(data)>limit:
            raise ProviderError('file_too_large')
        return data


def atomic(p, data):
    if p.is_symlink() or (p.exists() and (not p.is_file() or p.stat().st_uid != os.getuid())):
        raise ProviderError('unsafe_configuration_file')
    p.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if p.parent.resolve() != p.parent.absolute():
        raise ProviderError('configuration_symlink_parent')
    fd, tmp = tempfile.mkstemp(prefix='.agentj-provider-', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        with contextlib.suppress(FileNotFoundError):os.unlink(tmp)


def profile(pid):
    if not ID_RE.fullmatch(pid or ''):
        raise ProviderError('invalid_profile_id')
    return root()/pid


def define(pid, harness, url, model, api='openai'):
    if harness not in ('claude','codex','opencode') or api not in ('openai','anthropic'):
        raise ProviderError('invalid_harness_or_api')
    if not MODEL_RE.fullmatch(model or ''):
        raise ProviderError('invalid_model')
    url=_check_url(url)
    if harness=='claude' and api!='anthropic' or harness=='codex' and api!='openai':
        raise ProviderError('harness_api_mismatch')
    private_dir(root());p=profile(pid);private_dir(p)
    value={'id':pid,'harness':harness,'base_url':url,'model':model,'api':api}
    old=p/'profile.json'
    if old.exists() and json.loads(secure_read(old)) != value:
        raise ProviderError('profile_exists_use_a_new_id')
    atomic(old,json.dumps(value).encode())
    return {'id':pid,'harness':harness,'key_destination':'file:'+str(p/'key')}


def target(c):
    if c['harness']=='claude':return Path.home()/'.claude/settings.json'
    if c['harness']=='codex':return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'config.toml'
    return Path(config_file())


class ProbeError(ProviderError):
    def __init__(self,reason,band=None):
        super().__init__('provider_probe_'+reason)
        self.key_length_hint=band


def key_length_hint(key):
    # Only clearly abnormal sizes; custom providers do not share a standard key length.
    return '0-23' if len(key)<24 else '4097+' if len(key)>4096 else None


def probe(c,key):
    from .provider_runtime import public_url, NoRedirect
    band=key_length_hint(key)
    def fail(reason):raise ProbeError(reason,band) from None
    base=public_url(c['base_url'],'models')
    if not base:raise ProviderError('provider_url_not_public_https')
    headers={'User-Agent':'AgentJ-provider-profile','Content-Type':'application/json'}
    if c['api']=='anthropic':
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        body={'model':c['model'],'max_tokens':16,'messages':[{'role':'user','content':'Reply OK.'}]}
        endpoint=base[:-6]+'messages'
    else:
        headers['Authorization']='Bearer '+key
        body={'model':c['model'],'input':'Reply OK.','max_output_tokens':32}
        endpoint=base[:-6]+'responses'
    req=urllib.request.Request(endpoint,data=json.dumps(body).encode(),headers=headers)
    def status(code):
        if code in (401,403):fail('auth')
        if code==404:fail('model_or_endpoint')
        if code==400:fail('model')
        if code==429:fail('rate_limit')
        if 500<=code<=599:fail('server')
        if 300<=code<=399:fail('redirect')
        if code!=200:fail('http')
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=30) as r:
            status(r.status)
            try:value=json.loads(r.read(1024*1024+1))
            except (ValueError,UnicodeError):fail('bad_json')
            if not isinstance(value,dict) or not (value.get('output') if c['api']=='openai' else value.get('content')):fail('invalid_response')
    except urllib.error.HTTPError as e:
        # Never read upstream error body, URL or exception text (may contain key/content).
        status(e.code)
        fail('http')
    except (TimeoutError,socket.timeout):fail('timeout')
    except ssl.SSLError:fail('tls')
    except urllib.error.URLError as e:
        if isinstance(e.reason,(TimeoutError,socket.timeout)):fail('timeout')
        if isinstance(e.reason,ssl.SSLError):fail('tls')
        fail('network')
    except OSError:fail('network')
    return {'key_length_hint':band} if band else {}


def merge(c, key, old):
    if c['harness']=='codex':
        import tomlkit
        try:d=tomlkit.parse(old.decode())
        except Exception:raise ProviderError('codex_config_invalid') from None
        d['model_provider']=c['id'];d['model']=c['model']
        providers=d.setdefault('model_providers',tomlkit.table())
        entry=providers.setdefault(c['id'],tomlkit.table())
        entry.update(name=c['id'],base_url=c['base_url'],wire_api='responses',experimental_bearer_token=key,requires_openai_auth=False)
        entry.pop('env_key',None)
        return tomlkit.dumps(d).encode()
    try:d=json.loads(old or b'{}')
    except ValueError:raise ProviderError('native_config_invalid_json') from None
    if not isinstance(d,dict):raise ProviderError('native_config_not_object')
    if c['harness']=='claude':
        env=d.setdefault('env',{})
        if not isinstance(env,dict):raise ProviderError('native_env_not_object')
        base=c['base_url'].removesuffix('/v1')
        env.update(ANTHROPIC_BASE_URL=base,ANTHROPIC_AUTH_TOKEN=key)
        env.pop('ANTHROPIC_API_KEY',None)
        d['model']=c['model']
    else:
        providers=d.setdefault('provider',{})
        if not isinstance(providers,dict):raise ProviderError('native_provider_not_object')
        entry=block(c['id'],c['base_url'],[c['model']],c['api'])
        previous=providers.get(c['id'],{})
        if not isinstance(previous,dict):raise ProviderError('native_provider_not_object')
        entry={**previous,**entry,'models':{**previous.get('models',{}),**entry['models']},
               'options':{**previous.get('options',{}),**entry['options'],'apiKey':key}}
        providers[c['id']]=entry;d['model']=c['id']+'/'+c['model']
    return (json.dumps(d,ensure_ascii=False,indent=2)+'\n').encode()


def selection(c, model=None, write=False):
    from .state import State
    from .names import ctl_call
    st=State()
    result=ctl_call(st,{'cmd':'provider_model','kind':c['harness'],'write':write,'model':model},10)
    if result is not None:
        if not result.get('ok'):raise ProviderError('host_provider_selection_unavailable')
        return result
    cfg=st.agent_config() or {}
    applicable=cfg.get('kind')==c['harness']
    previous=cfg.get('model')
    if applicable and write:st.set_agent_model(model)
    return {'ok':True,'applicable':applicable,'model':previous}


def use(pid, verify=probe):
    p=profile(pid);c=json.loads(secure_read(p/'profile.json'))
    key=secure_read(p/'key',16384).decode().strip()
    if not key or any(ch.isspace() for ch in key):raise ProviderError('invalid_key_file')
    path=target(c)
    if path.is_symlink():raise ProviderError('unsafe_configuration_file')
    old=path.read_bytes() if path.exists() else b''
    merged=merge(c,key,old)
    selected=selection(c)
    probe_result=verify(c,key)     # no files changed if the real request fails
    if (path.read_bytes() if path.exists() else b'')!=old:raise ProviderError('config_changed_during_probe')
    backup=p/'backups';private_dir(backup)
    b=backup/(str(time.time_ns()));private_dir(b)
    atomic(b/'before',old)
    record={'target':str(path),'existed':path.exists(),'after_sha256':hashlib.sha256(merged).hexdigest(),'harness':c['harness'],'agent_model_before':selected.get('model'),'agent_applicable':selected.get('applicable',False)}
    atomic(b/'record.json',json.dumps(record).encode())
    atomic(path,merged)
    mid=c['id']+'/'+c['model'] if c['harness']=='opencode' else c['model']
    try:
        selection(c,mid,True)
    except Exception:
        if record['existed']:atomic(path,old)
        else:path.unlink()
        raise
    record['agent_model_after']=mid
    atomic(b/'record.json',json.dumps(record).encode())
    return {'id':pid,'harness':c['harness'],'verified':True,'backup':b.name,'restart_required':True,**(probe_result or {})}


def restore(pid):
    p=profile(pid);backups=p/'backups'
    choices=sorted(x for x in backups.iterdir() if x.is_dir() and (x/'record.json').exists()) if backups.exists() else []
    if not choices:raise ProviderError('no_profile_backup')
    b=choices[-1];r=json.loads(secure_read(b/'record.json'));path=Path(r['target'])
    if path.is_symlink() or not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=r['after_sha256']:
        raise ProviderError('config_changed_since_switch_restore_refused')
    c={'harness':r['harness']}
    selected=selection(c)
    if r.get('agent_applicable') and selected.get('model') != r.get('agent_model_after'):
        raise ProviderError('agent_model_changed_since_switch')
    old=secure_read(b/'before')
    if r.get('agent_applicable'):selection(c,r.get('agent_model_before'),True)
    if r['existed']:atomic(path,old)
    else:path.unlink()
    atomic(b/'restored',b'1');(b/'record.json').rename(b/'restored-record.json')
    return {'id':pid,'restored':True,'restart_required':True}


def listing():
    if not root().exists():return []
    out=[]
    for p in sorted(root().iterdir()):
        if p.is_dir() and not p.is_symlink() and (p/'profile.json').is_file():
            c=json.loads(secure_read(p/'profile.json'));out.append({k:c[k] for k in ('id','harness','base_url','model','api')})
    return out


def execute(spec):
    """Fixed native targets only; runs in the host, never returns key/config content."""
    try:
        if not isinstance(spec,dict) or spec.get('action') not in ('save','use','restore','list'):
            raise ProviderError('invalid_profile_action')
        private_dir(root())
        fd=os.open(root()/'.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            action=spec['action']
            if action=='save':
                result=define(spec.get('id'),spec.get('harness'),spec.get('base_url') or PRESETS.get(spec.get('preset')),spec.get('model'),spec.get('api','openai'))
            elif action=='use':result=use(spec.get('id'),verify=probe)
            elif action=='restore':result=restore(spec.get('id'))
            else:result={'profiles':listing()}
        return {'ok':True,**result}
    except Exception as e:
        reason=str(e) if isinstance(e,ProviderError) else type(e).__name__
        return {'ok':False,'reason':reason,**({'key_length_hint':e.key_length_hint} if isinstance(e,ProbeError) and e.key_length_hint else {})}


def command(args):
    ap=argparse.ArgumentParser(description='Local native provider profiles; key only through phone secret card')
    sub=ap.add_subparsers(dest='action',required=True)
    add=sub.add_parser('save');add.add_argument('id');add.add_argument('--harness',choices=['claude','codex','opencode'],required=True)
    add.add_argument('--base-url');add.add_argument('--preset',choices=list(PRESETS));add.add_argument('--model',required=True)
    add.add_argument('--api',choices=['openai','anthropic'],default='openai')
    for name in ('use','restore'):sub.add_parser(name).add_argument('id')
    sub.add_parser('list')
    a=ap.parse_args(args)
    from .state import State
    from . import elevate
    st=State()
    spec=vars(a)
    if (st.perm_dir/elevate.SOCK_NAME).exists():
        # The existing Agent-facing socket is bound through the data fence.
        # A timed-out host request must never fall back to a second local write.
        result=elevate.client_request(st,{'t':'provider_profile','spec':spec},timeout=90)
        if not isinstance(result,dict) or 'ok' not in result:
            result={'ok':False,'reason':'host_profile_unavailable'}
    else:
        result=execute(spec)
    print(json.dumps(result));return 0 if result.get('ok') else 2
