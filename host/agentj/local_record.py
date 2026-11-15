"""Fresh owner-approved private records. No installed-code mutation or repair claim."""
from __future__ import annotations
import asyncio
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import time

from . import local_patches

TOOL='agentj local record'
INSTALL_ROOT=Path(__file__).resolve().parents[1]
LIMIT=256*1024
MAX_FILES=20
DIR=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
HEX=re.compile(r'[0-9a-f]{64}\Z')

class Refused(ValueError):
    pass

def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def sha(value):return hashlib.sha256(value).hexdigest()

def strings(value):
    if isinstance(value,str):yield value
    elif isinstance(value,dict):
        for k,v in value.items():yield from strings(k);yield from strings(v)
    elif isinstance(value,list):
        for v in value:yield from strings(v)

def relative(value):
    if (not isinstance(value,str) or not local_patches._target(value) or not value
            or str(PurePosixPath(value))!=value or len(value)>240 or not all(c.isprintable() for c in value)):raise Refused('unsafe_relative_target')
    return value

def absolute(value):
    if (not isinstance(value,str) or not value.startswith('/') or str(Path(value))!=value
            or '..' in Path(value).parts or not all(c.isprintable() for c in value) or len(value)>4096):raise Refused('unsafe_root')
    return Path(value)

def directory(path, private=False):
    """Walk every absolute component, not just its leaf, without following links."""
    path=absolute(str(path));fd=os.open('/',DIR)
    try:
        for part in path.parts[1:]:
            nxt=os.open(part,DIR,dir_fd=fd);os.close(fd);fd=nxt
        info=os.fstat(fd)
        if info.st_uid!=os.getuid() or info.st_mode&0o022 or private and info.st_mode&0o077:
            raise Refused('unsafe_directory')
        return fd
    except BaseException:
        os.close(fd);raise

def ident(fd):
    s=os.fstat(fd);return [s.st_dev,s.st_ino]

def current(path,fd):
    try:
        now=directory(path)
        try:return ident(now)==ident(fd)
        finally:os.close(now)
    except (OSError,Refused):return False

def read(fd,path):
    parts=relative(path).split('/');parent=os.dup(fd)
    try:
        for part in parts[:-1]:
            nxt=os.open(part,DIR,dir_fd=parent);os.close(parent);parent=nxt
        f=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            before=os.fstat(f)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1
                    or before.st_size>LIMIT or before.st_mode&0o022):raise Refused('unsafe_file')
            raw=b''
            while len(raw)<=LIMIT:
                chunk=os.read(f,LIMIT+1-len(raw))
                if not chunk:break
                raw+=chunk
            after=os.fstat(f);named=os.stat(parts[-1],dir_fd=parent,follow_symlinks=False)
            if len(raw)>LIMIT or any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_nlink')) or (named.st_dev,named.st_ino)!=(after.st_dev,after.st_ino):raise Refused('source_changed')
            raw.decode('utf-8')
            if b'\x00' in raw:raise Refused('binary_refused')
            return raw
        finally:os.close(f)
    finally:os.close(parent)

def scan(work,values):
    from . import peer_guard
    index=peer_guard.SecretIndex.build(workdir=work,fragment_size=8);index.require_complete()
    for value in values:
        text=value.decode('utf-8') if isinstance(value,bytes) else value
        if not peer_guard.outbound(text,index).ok:raise Refused('secret_refused')
    index.require_complete()

def inventory(fd,prefix='',max_files=MAX_FILES):
    out={}
    for name in sorted(os.listdir(fd)):
        path=relative(prefix+name);info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child=os.open(name,DIR,dir_fd=fd)
            try:out.update(inventory(child,path+'/',max_files))
            finally:os.close(child)
        elif stat.S_ISREG(info.st_mode):out[path]=read(fd,name)
        else:raise Refused('unsafe_feature')
        if len(out)>max_files or sum(map(len,out.values()))>LIMIT:raise Refused('inventory_too_large')
    return out

def prepare(value,work):
    if not isinstance(value,dict) or len(encode(value))>16000:raise Refused('invalid_contract')
    value=copy.deepcopy(value);kind=value.get('kind')
    common={'kind','description','validation','rollback'}
    typed=kind=='feature' and 'entry_kind' in value
    wanted=common|({'baseline_root','baseline_sha256','files','father_ticket'} if kind=='patch' else
                   {'source','entry_kind','harness','skill_name'} if typed else {'source','entry'} if kind=='feature' else set())
    if kind not in ('patch','feature') or set(value)!=wanted:raise Refused('invalid_contract')
    for key in ('description','validation','rollback'):
        if not isinstance(value[key],str) or not value[key].strip() or len(value[key])>1000:raise Refused('invalid_description')
    install=absolute(str(INSTALL_ROOT));installed=directory(install)
    try:
        record={'kind':kind,'install_root':str(install),'install_identity':ident(installed)}
        payload={}
        if kind=='patch':
            files=value['files'];proof=value['baseline_sha256'];ticket=value['father_ticket']
            if (not isinstance(files,list) or not 1<=len(files)<=MAX_FILES or len(set(files))!=len(files)
                    or not isinstance(proof,dict) or set(proof)!=set(files) or not isinstance(ticket,str)
                    or not re.fullmatch(r'[A-Za-z0-9-]{1,80}',ticket)):raise Refused('invalid_patch_contract')
            baseline=absolute(value['baseline_root']);base=directory(baseline)
            try:
                record.update(files=files,baseline_identity=ident(base),baseline_origin='owner_local_sha256')
                old,new={},{}
                for path in files:
                    relative(path)
                    if not isinstance(proof[path],str) or not HEX.fullmatch(proof[path]):raise Refused('invalid_baseline_hash')
                    old[path]=read(base,path);new[path]=read(installed,path)
                    if sha(old[path])!=proof[path]:raise Refused('baseline_hash_mismatch')
                if sum(map(len,old.values()))+sum(map(len,new.values()))>LIMIT:raise Refused('inventory_too_large')
                diff=''.join(''.join(difflib.unified_diff(old[p].decode().splitlines(True),new[p].decode().splitlines(True),fromfile='a/'+p,tofile='b/'+p)) for p in files).encode()
                if not diff:raise Refused('no_changes')
                # Keep final-line semantics unambiguous; later apply must not guess them.
                if any(raw and not raw.endswith(b'\n') for raw in (*old.values(),*new.values())):raise Refused('newline_required')
                payload={'fix.patch':diff,**{'base/'+p:raw for p,raw in old.items()}}
                record.update(baseline_sha256={p:sha(raw) for p,raw in old.items()},target_sha256={p:sha(raw) for p,raw in new.items()},father_ticket=ticket)
                if not current(baseline,base):raise Refused('source_changed')
            finally:os.close(base)
        else:
            source=absolute(value['source'])
            if typed:
                from . import local_relink
                mapped=local_relink.mapping(value);entry=None
            else:entry=relative(value['entry'])
            if source==install or install in source.parents:raise Refused('feature_inside_installation')
            fd=directory(source)
            try:
                files=inventory(fd)
                if not files:raise Refused('empty_feature')
                record.update(path=str(source),entry=mapped['entry'] if typed else str(install/entry),source_identity=ident(fd),source_sha256={p:sha(raw) for p,raw in files.items()})
                if typed:
                    record.update(mapped)
                    record['source_facts']=local_relink.facts(fd)
                    verified,source_identity,source_files=local_relink.source(record)
                    if source_identity!=ident(fd) or source_files!=files:raise Refused('source_changed')
                if not current(source,fd):raise Refused('source_changed')
            finally:os.close(fd)
            scan(work,[*files.keys(),*files.values()])
        if not current(install,installed):raise Refused('source_changed')
        from . import __version__
        record['runtime_version']=__version__
        payload['README.md']=('Local record, not official repair proof / 本地记录，不是官方修复证明\n\n'+''.join(k+': '+value[k]+'\n' for k in ('description','validation','rollback'))).encode()
        scan(work,[*strings(value),*strings(record),*payload.keys(),*payload.values()])
        if sum(map(len,payload.values()))+len(encode(record))>LIMIT-1024:raise Refused('inventory_too_large')
        record['payload_sha256']={p:sha(raw) for p,raw in payload.items()}
        return record,payload
    finally:os.close(installed)

def manifest(fd):
    try:raw=read(fd,'MANIFEST.json')
    except FileNotFoundError:return None,{'schema':1,'patches':[],'features':[]}
    value=json.loads(raw)
    if (not isinstance(value,dict) or set(value)!={'schema','patches','features'} or value.get('schema')!=1
            or any(not isinstance(value.get(k),list) or len(value[k])>=100 for k in ('patches','features'))):raise Refused('invalid_manifest')
    for kind in ('patches','features'):
        ids=set()
        for row in value[kind]:
            if (not isinstance(row,dict) or not local_patches.IDENT.fullmatch(str(row.get('id','')))
                    or not row['id'].startswith('p' if kind=='patches' else 'f') or row['id'] in ids):raise Refused('invalid_manifest')
            ids.add(row['id'])
    return raw,value

def workspace_snapshot():
    home=Path.home();hfd=directory(home)
    try:
        try:fd=os.open('.agentj-local',DIR,dir_fd=hfd)
        except FileNotFoundError:return None,None
        try:
            info=os.fstat(fd)
            if info.st_uid!=os.getuid() or info.st_mode&0o077:raise Refused('unsafe_workspace')
            raw,_=manifest(fd);return ident(fd),sha(raw or b'')
        finally:os.close(fd)
    finally:os.close(hfd)

def write(fd,path,raw):
    parts=relative(path).split('/');parent=os.dup(fd)
    try:
        for part in parts[:-1]:
            try:os.mkdir(part,0o700,dir_fd=parent)
            except FileExistsError:pass
            nxt=os.open(part,DIR,dir_fd=parent);os.close(parent);parent=nxt
        leaf=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            with os.fdopen(leaf,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(leaf)
            written_identity=ident(leaf)
        finally:os.close(leaf)
        os.fsync(parent)
        return written_identity
    finally:os.close(parent)

def named_identity(fd,name):
    row=os.stat(name,dir_fd=fd,follow_symlinks=False)
    return [row.st_dev,row.st_ino]

def exchange(fd,a,b):
    import ctypes
    fn=ctypes.CDLL(None,use_errno=True).renameat2
    fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];fn.restype=ctypes.c_int
    if fn(fd,os.fsencode(a),fd,os.fsencode(b),2)!=0:raise Refused('manifest_exchange_unavailable')

def commit(st,value,work,expected,expected_workspace,live):
    from . import bounded_plan
    with st.file_lock('local-patches'):
        if not live():raise Refused('context_changed')
        actual,payload=prepare(value,work)
        if actual!=expected or workspace_snapshot()!=expected_workspace:raise Refused('source_changed')
        home=Path.home();hfd=directory(home)
        try:
            if not current(home,hfd):raise Refused('context_changed')
            try:os.mkdir('.agentj-local',0o700,dir_fd=hfd)
            except FileExistsError:pass
            root=os.open('.agentj-local',DIR,dir_fd=hfd)
            try:
                if os.fstat(root).st_uid!=os.getuid() or os.fstat(root).st_mode&0o077:raise Refused('unsafe_workspace')
                raw,data=manifest(root)
                if expected_workspace[0] is not None and ident(root)!=expected_workspace[0] or sha(raw or b'')!=(expected_workspace[1] or sha(b'')):raise Refused('source_changed')
                kind='patches' if actual['kind']=='patch' else 'features';prefix='p' if kind=='patches' else 'f'
                used={row['id'] for row in data[kind]};number=next((i for i in range(1,102) if prefix+str(i) not in used),None)
                name=prefix+str(number);record={**actual,'id':name,'status':'pending_review'}
                if kind=='patches':record['patch']='patches/'+name+'/fix.patch'
                else:record['kind']='symlink'
                row={k:v for k,v in record.items() if k not in ('payload_sha256','install_identity','baseline_identity')}
                data[kind].append(row)
                stage='.record-'+secrets.token_hex(12);os.mkdir(stage,0o700,dir_fd=root);held=os.open(stage,DIR,dir_fd=root)
                try:
                    content={**payload,'record.json':encode(record)+b'\n'}
                    for path,body in content.items():write(held,path,body)
                    if inventory(held,max_files=MAX_FILES+3)!=content or not live():raise Refused('context_changed')
                    scan(work,[*content.keys(),*content.values(),*strings(row)])
                    if prepare(value,work)[0]!=expected:raise Refused('source_changed')
                    if not current(home/'.agentj-local',root):raise Refused('workspace_changed')
                    try:os.mkdir(kind,0o700,dir_fd=root)
                    except FileExistsError:pass
                    target=os.open(kind,DIR,dir_fd=root)
                    try:
                        bounded_plan._rename_exclusive(root,stage,target,name)
                        verify=os.open(name,DIR,dir_fd=target)
                        try:
                            if ident(verify)!=ident(held) or inventory(verify,max_files=MAX_FILES+3)!=content:raise Refused('record_recovery_required')
                        finally:os.close(verify)
                        os.fsync(target)
                    finally:os.close(target)
                    candidate='.manifest-'+secrets.token_hex(12);newraw=encode(data)+b'\n'
                    scan(work,[newraw,*strings(data)])
                    new_identity=write(root,candidate,newraw)
                    if (not live() or not current(home/'.agentj-local',root)
                            or named_identity(root,candidate)!=new_identity):raise Refused('record_recovery_required')
                    # Keep the displaced manifest as a private recovery receipt. Never unlink
                    # a mutable name or perform a byte-only rollback that may move an unknown object.
                    if raw is None:bounded_plan._rename_exclusive(root,candidate,root,'MANIFEST.json')
                    else:
                        old_fd=os.open('MANIFEST.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
                        try:
                            old_identity=ident(old_fd)
                            if os.read(old_fd,LIMIT+1)!=raw:raise Refused('record_recovery_required')
                            exchange(root,candidate,'MANIFEST.json')
                            if (read(root,candidate)!=raw or named_identity(root,candidate)!=old_identity):raise Refused('record_recovery_required')
                        finally:os.close(old_fd)
                    if (read(root,'MANIFEST.json')!=newraw or named_identity(root,'MANIFEST.json')!=new_identity
                            or not current(home/'.agentj-local',root)):raise Refused('record_recovery_required')
                    verify_parent=os.open(kind,DIR,dir_fd=root)
                    try:
                        verify=os.open(name,DIR,dir_fd=verify_parent)
                        try:
                            if ident(verify)!=ident(held) or inventory(verify,max_files=MAX_FILES+3)!=content:raise Refused('record_recovery_required')
                        finally:os.close(verify)
                    finally:os.close(verify_parent)
                    os.fsync(root)
                    return {'ok':True,'id':name,'kind':actual['kind'],'applied':False,'official_fix_verified':False}
                finally:os.close(held)
            finally:os.close(root)
        finally:os.close(hfd)

async def handle(host,request):
    from . import application_ingress, task_create, codex_perm, provenance
    try:
        if not sys.platform.startswith('linux'):raise Refused('record_platform_unsupported')
        frame=application_ingress.current(host)
        if frame is None or frame.kind=='task_local' or not isinstance(request,dict) or set(request)!={'cmd','contract','sha256'} or request['cmd']!='local_record':raise Refused('owner_application_required')
        actor=host.agent;send=getattr(actor,'cur_send',None)
        if frame.kind!='terminal' and not provenance.owner_intent(provenance.turn_source(actor),send):raise Refused('owner_application_required')
        work=(host.agent_cfg or {}).get('dir');cfg=host.agent_cfg
        first=task_create._write_ceiling(host,work)
        if codex_perm.mode_of(first[1].get('sandbox'))!='danger-full-access':raise Refused('native_full_access_required')
        value=copy.deepcopy(request['contract'])
        if request['sha256']!=sha(encode(value)):raise Refused('contract_hash_mismatch')
        prepared,payload=await asyncio.to_thread(prepare,value,work)
        old_workspace=await asyncio.to_thread(workspace_snapshot)
        inp={'purpose':'private_local_record','kind':prepared['kind'],'installation':prepared['install_root'],'record_sha256':sha(encode(prepared)),
             'files':prepared.get('files') or list(prepared.get('source_sha256',{})), 'source':prepared.get('path') or value.get('baseline_root'),
             'description':value['description'],'validation':value['validation'],'rollback':value['rollback'],
             'entry':prepared.get('entry'),'baseline_origin':prepared.get('baseline_origin'),'effect':'Record only; no apply, relink, commands or official repair claim. / 仅保存记录，不应用、不挂接、不执行命令、不宣称官方修复。'}
        def context_live():
            try:
                ceiling=task_create._write_ceiling(host,work)
                return (frame.worker_live() and getattr(actor,'cur_send',None) is send
                        and (frame.kind=='terminal' or provenance.owner_intent(provenance.turn_source(actor),send)) and host.agent_cfg is cfg and all(a is b for a,b in zip(first[:3],ceiling[:3])) and first[3]==ceiling[3])
            except (task_create.Refused,OSError):return False
        input_sha=sha(encode(inp))
        answer=await host.ask(TOOL,inp,grant_info=True,batch=False,always_ask=True,require_passkey=True)
        grant=answer.get('grant_info') or {};until=time.monotonic()+min(30,host.ask_ttl)
        if (sha(encode(inp))!=input_sha or answer.get('behavior')!='allow' or grant.get('uv') is not True or not grant.get('device') or not grant.get('rid') or not grant.get('sign_pub') or host.st.sign_key(grant['device'])!=grant['sign_pub']):raise Refused('fresh_owner_uv_required')
        def live():return context_live() and time.monotonic()<=until and host.st.sign_key(grant['device'])==grant['sign_pub']
        result=await asyncio.to_thread(commit,host.st,value,work,prepared,old_workspace,live)
        host.st.log('local_record_created',id=result['id'],kind=result['kind'],rid=grant['rid'])
        return result
    except Refused as exc:return {'ok':False,'error':str(exc)}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):return {'ok':False,'error':'local_record_unavailable'}

def command(args):
    from . import names
    from .state import State
    try:
        if not args.contract or getattr(args,'id',None):raise Refused('contract_required')
        fd=os.open(args.contract,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>16000:raise Refused('invalid_contract')
            raw=os.read(fd,16001)
        finally:os.close(fd)
        value=json.loads(raw);result=names.ctl_call(State(),{'cmd':'local_record','contract':value,'sha256':sha(encode(value))},timeout=900)
        if not result:result={'ok':False,'error':'host_unavailable'}
    except names.ServeBusy:result={'ok':False,'error':'outcome_unknown_inspect_inventory_before_retry'}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError):result={'ok':False,'error':'invalid_record_contract'}
    print(json.dumps(result,ensure_ascii=False));return 0 if result.get('ok') else 2
